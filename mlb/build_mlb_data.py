/* JTT MLB — scoring.js  (window.JTTScoring)
   Batter-vs-pitcher engine adapted to the unified shell's data + JTTScoring contract.
   Batter props: blended per-game rate x platoon-split x park/weather x opposing-arm factors,
   turned into a Poisson P(>= line) and blended with the empirical hit rate. Pitcher Ks:
   K/9 x expected IP x opponent whiff-rate. Team "DVP" = what an opponent allows vs league. */
window.JTTScoring = (function () {
  'use strict';
  var LG_OPPAVG = .245, LG_KRATE = .225, LG_HR9 = 1.15, LG_BB9 = 3.1, LG_K9 = 8.6;
  var RECENT_WIN = 15;   // game-log window for "recent" rates

  var players = [], teams = [], dvp = [], logs = {}, fixture = [], meta = {}, curSeason = '2026';
  var byName = {}, byTeamGame = {}, teamByAbbr = {}, dvpByTeam = {}, dvpAvg = {};

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

  // Bayesian shrinkage: recent per-game sum blended toward season baseline (prior 7 games)
  function blendedRate(p, stat) {
    var s = slice(p.name), sum = 0, n = s.length;
    for (var i = 0; i < n; i++) sum += (s[i][stat] || 0);
    var seasonPG = (p.matches ? (p[stat] || 0) / p.matches : (n ? sum / n : 0));
    var prior = 7;
    return (sum + seasonPG * prior) / (n + prior);
  }

  function gameForTeam(team) { return byTeamGame[team] || null; }
  function starterFacing(bat) {
    var g = gameForTeam(bat.team); if (!g) return null;
    var oppPitcherName = (g.home === bat.team) ? g.awayPitcher : g.homePitcher;
    return oppPitcherName ? byName[oppPitcherName] : null;
  }
  function parkFor(team) { var g = gameForTeam(team); return (g && g.park) || { hrFactor: 1, runFactor: 1 }; }
  function weatherMult(team) { var g = gameForTeam(team); return (g && g.weather && g.weather.hrMult) ? g.weather.hrMult : 1; }
  function platoon(bat, throws) { return throws === 'L' ? bat.splitVsL : bat.splitVsR; }

  // ---- batter projection ----
  function projBat(bat, stat) {
    var base = blendedRate(bat, stat), f = 1;
    var pit = starterFacing(bat), pk = parkFor(bat.team), wm = weatherMult(bat.team);
    if (pit) {
      if (stat === 'HR' || stat === 'TB') {
        var sp = platoon(bat, pit.throws);
        f *= bat.SLG ? _clamp((sp && sp.SLG ? sp.SLG : bat.SLG) / bat.SLG, .8, 1.3) : 1;
        f *= (stat === 'HR' ? pk.hrFactor * wm : (1 + (pk.hrFactor * wm - 1) * 0.5));
        f *= pit.HR9 ? _clamp(pit.HR9 / LG_HR9, .8, 1.3) : 1;
      } else if (stat === 'BB') {
        f *= pit.BB9 ? _clamp(pit.BB9 / LG_BB9, .8, 1.35) : 1;
      } else if (stat !== 'SB') { // H, RBI, R
        var psp = bat.bats === 'L' ? pit.splitVsL : pit.splitVsR;
        f *= (psp && psp.oppAVG) ? _clamp(psp.oppAVG / LG_OPPAVG, .85, 1.2) : 1;
        f *= _clamp(9.5 / ((pit.K9 || LG_K9) + 0.5), .88, 1.08);
        f *= (1 + (pk.runFactor - 1) * 0.4);
      }
    }
    return { proj: base * f, pit: pit, pk: pk };
  }
  function batProb(bat, stat, line) {
    var r = projBat(bat, stat), model = poissonAtLeast(r.proj, Math.ceil(line));
    if (stat === 'HR' || stat === 'SB' || stat === 'BB') return _clamp(model, .01, .99);
    var emp = hitRateLogs(bat.name, stat, line);
    return _clamp(0.6 * model + 0.4 * emp, .01, .99);
  }

  // ---- pitcher strikeout projection ----
  function pitcherKProj(pit) {
    var s = slice(pit.name), ip = 0, k = 0;
    for (var i = 0; i < s.length; i++) { ip += (s[i].IP || 0); k += (s[i].K || 0); }
    var recentK9 = ip ? k / ip * 9 : (pit.K9 || LG_K9), n = s.length;
    var k9 = (n * recentK9 + 3 * (pit.K9 || LG_K9)) / (n + 3);
    var expIP = s.length ? ip / s.length : (pit.matches ? (pit.IP || 0) / pit.matches : 5.5);
    var g = gameForTeam(pit.team), oppAbbr = g ? (g.home === pit.team ? g.away : g.home) : null;
    var oppRow = oppAbbr ? teamByAbbr[oppAbbr] : null;
    var oppF = (oppRow && oppRow.kRate) ? _clamp(oppRow.kRate / LG_KRATE, .85, 1.2) : 1;
    var proj = k9 / 9 * expIP * oppF;
    return { proj: proj, k9: k9, line: Math.max(3.5, Math.round((proj - 0.5) * 2) / 2), opp: oppAbbr };
  }

  // ---- interface ----
  function scoreCMP(p, stat, line, opp) {
    if (!p) return 0;
    var prob;
    if (p.role === 'pitch' && stat === 'K') {
      var kp = pitcherKProj(p), emp = hitRateLogs(p.name, 'K', line), model = poissonAtLeast(kp.proj, Math.ceil(line));
      prob = _clamp(0.6 * model + 0.4 * emp, .01, .99);
    } else if (p.role !== 'pitch') { prob = batProb(p, stat, line); }
    else { prob = hitRateLogs(p.name, stat, line); }
    return (prob - 0.5) * 20;   // 0.5->0, 0.62->2.4, 0.775->5.5
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
  var DR_LINE = { H: 1.5, TB: 1.5, HR: 0.5, RBI: 0.5, R: 0.5, SB: 0.5, BB: 0.5 };
  function drLine(avg, stat) {
    if (stat === 'K') { return (avg >= 4) ? Math.max(3.5, Math.round((avg - 0.5) * 2) / 2) : null; }
    var l = DR_LINE[stat != null ? stat : 'H']; return l != null ? l : 0.5;
  }
  function getHitRate(name, stat, line, curOnly) {
    var games = logsOf(name);
    if (curOnly) games = games.filter(function (g) { return String(g.Year) === String(curSeason); });
    if (games.length < 3) return null;
    var hits = games.filter(function (g) { return (g[stat] || 0) >= line; }).length;
    return { rate: hits / games.length, n: games.length };
  }
  function getRecentAvg(name, stat, n) {
    var a = logsOf(name).slice(-(n || RECENT_WIN)).map(function (r) { return r[stat]; }).filter(function (v) { return v != null; });
    return a.length ? a.reduce(function (s, v) { return s + v; }, 0) / a.length : null;
  }
  function getDVPPct(oppTeam, pos, stat) {
    var row = dvpByTeam[oppTeam]; if (!row) return null;
    var val = row[stat]; if (val == null) return null;
    var avg = dvpAvg[stat]; if (avg == null || avg === 0) return null;
    return ((val - avg) / avg) * 100;   // opp allows more than league = softer for the hitter
  }
  function muInfo(pct) {
    if (pct === null || pct === undefined) return null;
    if (pct > 12) return { t: 'Soft', c: '#22c55e', cl: 'c-soft' };
    if (pct > 4) return { t: 'Fav', c: '#eab308', cl: 'c-fav' };
    if (pct > -4) return { t: 'Neutral', c: '#555', cl: 'c-neu' };
    if (pct > -12) return { t: 'Tough', c: '#f97316', cl: 'c-tough' };
    return { t: 'V.Tough', c: '#ef4444', cl: 'c-vtough' };
  }
  // matchup tags for a batter (platoon edge, park, hittable/tough arm)
  function getContextSignals(p, stat, line, opp) {
    var tags = []; if (!p || p.role === 'pitch') return tags;
    var pit = starterFacing(p), pk = parkFor(p.team);
    if (pit) {
      var sp = platoon(p, pit.throws);
      if (sp && p.SLG && sp.SLG / p.SLG >= 1.12) tags.push({ t: 'Platoon edge vs ' + pit.throws + 'HP', good: true });
      if (sp && p.SLG && sp.SLG / p.SLG <= 0.9) tags.push({ t: 'Platoon fade vs ' + pit.throws + 'HP', good: false });
      var psp = p.bats === 'L' ? pit.splitVsL : pit.splitVsR;
      if (psp && psp.oppAVG >= LG_OPPAVG + 0.02) tags.push({ t: 'Hittable arm', good: true });
      if (pit.K9 && pit.K9 >= LG_K9 + 1) tags.push({ t: 'Whiff-heavy arm', good: false });
    }
    if ((stat === 'HR' || stat === 'TB') && pk && pk.hrFactor >= 1.05) tags.push({ t: 'Hitter park', good: true });
    if ((stat === 'HR' || stat === 'TB') && pk && pk.hrFactor <= 0.95) tags.push({ t: 'Pitcher park', good: false });
    return tags;
  }
  function cmpFactors(p, stat, line, opp, lbl) {
    var out = []; if (!p) return out;
    var av = avgOf(p.name, stat), hr = getHitRate(p.name, stat, line, false);
    out.push({ label: 'Recent avg', val: av.toFixed(2), good: null });
    if (hr) out.push({ label: 'Hit rate (window)', val: Math.round(hr.rate * 100) + '% (' + hr.n + ')', good: hr.rate >= .6 });
    if (p.role === 'pitch') {
      var kp = pitcherKProj(p);
      out.push({ label: 'Proj Ks', val: kp.proj.toFixed(1) + ' (K/9 ' + kp.k9.toFixed(1) + ')', good: kp.proj >= line });
      if (kp.opp) out.push({ label: 'Opp lineup', val: 'vs ' + kp.opp, good: null });
    } else {
      var r = projBat(p, stat);
      out.push({ label: 'Projection', val: r.proj.toFixed(2), good: r.proj >= line });
      var tags = getContextSignals(p, stat, line, opp);
      tags.forEach(function (tg) { out.push({ label: 'Matchup', val: tg.t, good: tg.good }); });
    }
    return out;
  }
  function getL() { return null; }              // no line-shopping source in the shell yet (odds feed lands later)
  var POS = ['SP', 'RP', 'C', '1B', '2B', '3B', 'SS', 'LF', 'CF', 'RF', 'DH'];
  var POS_TO_DVP = {};                          // MLB DVP is team-level; identity/no-op

  function probableNames() {
    var out = [];
    fixture.forEach(function (g) { if (g.homePitcher) out.push(g.homePitcher); if (g.awayPitcher) out.push(g.awayPitcher); });
    return out;
  }
  function oppOfTeam(team) { var g = gameForTeam(team); return g ? (g.home === team ? g.away : g.home) : null; }

  function configure(ctx) {
    ctx = ctx || {};
    players = ctx.players || []; teams = ctx.teams || []; dvp = ctx.dvp || [];
    logs = ctx.logsByName || {}; fixture = ctx.fixture || []; meta = ctx.meta || {};
    curSeason = ctx.currentSeason || '2026';
    byName = {}; players.forEach(function (p) { byName[p.name] = p; });
    byTeamGame = {};
    fixture.forEach(function (g) { if (g.home) byTeamGame[g.home] = g; if (g.away) byTeamGame[g.away] = g; });
    teamByAbbr = {}; teams.forEach(function (t) { teamByAbbr[t.team] = t; });
    // DVP table (team allowances) + league averages per stat
    dvpByTeam = {}; var sums = {}, cnt = {};
    dvp.forEach(function (row) {
      dvpByTeam[row.team] = row;
      ['R', 'H', 'HR', 'BB', 'SO'].forEach(function (k) { if (row[k] != null) { sums[k] = (sums[k] || 0) + row[k]; cnt[k] = (cnt[k] || 0) + 1; } });
    });
    dvpAvg = {}; Object.keys(sums).forEach(function (k) { dvpAvg[k] = sums[k] / cnt[k]; });
  }

  return {
    configure: configure, verdict: verdict, scoreCMP: scoreCMP, drLine: drLine,
    getHitRate: getHitRate, getRecentAvg: getRecentAvg, getL5Avg: function(name, stat){ return getRecentAvg(name, stat, 5); }, getDVPPct: getDVPPct,
    muInfo: muInfo, getContextSignals: getContextSignals, cmpFactors: cmpFactors,
    getL: getL, POS: POS, POS_TO_DVP: POS_TO_DVP,
    // MLB extras used by mlb/signals.js
    projBat: projBat, batProb: batProb, pitcherKProj: pitcherKProj, starterFacing: starterFacing, parkFor: parkFor, probableNames: probableNames, oppOfTeam: oppOfTeam
  };
})();
