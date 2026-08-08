/* JTT MLB — signals.js  (window.JTTSignals)
   Degen tiles for the unified shell, ported to match the standalone /mlb/ engine.
   Batter/pitcher signals driven by mlb/scoring.js + deps.logsFor (gamelogs) + player splits/bvp/statcast.
   Tiles: Hot Bats, Cold Bats, Whiff Risk, K Targets, Long Ball, Platoon Edge, Hittable Arms,
          Table Setters, Bunnies, Bogeys, Wheels, Due/Unlucky*, Running Hot*  (* need Statcast in the data build). */
window.JTTSignals = (function () {
  'use strict';
  function create(deps) {
    deps = deps || {};
    var esc = deps.esc || function (s) { return s == null ? '' : String(s); };
    var emptyState = deps.emptyState || function () { return ''; };
    var degWrap = deps.degWrap || function (i, t, items) { return (items && items.length) ? items.join('') : ''; };
    var posShort = deps.posShort || function (p) { return p || ''; };
    var abbr = deps.abbr || function (t) { return t || ''; };
    var _degBadges = deps._degBadges || function () { return ''; };
    var _fixtureSet = deps._fixtureSet || function () { return { has: function () { return true; } }; };
    var byName = deps.byName || function () { return null; };
    var logsFor = deps.logsFor || function () { return []; };
    var oddsFor = deps.oddsFor || function () { return null; };
    var degRow = deps.degRow || function () { return ''; };
    var players = deps.players || [];
    var JS = deps.JTTScoring || window.JTTScoring;

    function slateBats() {
      var teams = _fixtureSet();
      return (players || []).filter(function (p) { return p.role === 'bat' && teams.has(p.team) && (p.matches || 0) >= 15; });
    }
    function _oddsStr(name, mkt) {
      if (!mkt) return '';
      var o = oddsFor(name, mkt);
      return (o && o.over != null) ? '$' + (+o.over).toFixed(2) + (o.book ? ' ' + esc(o.book) : '') : '';
    }
    // render each signal as the shell's standard degRow (same look as AFL/NFL; $odds auto-highlighted)
    function card(c, col) {
      var od = _oddsStr(c.p.name, c.mkt);
      var sub = posShort(c.p.position) + ' \u00b7 ' + abbr(c.p.team) + (c.opp ? ' v ' + abbr(c.opp) : '') + ' \u00b7 <b>' + c.headline + '</b> \u00b7 ' + c.detail;
      var right = od || c.headline;
      return degRow(c.p.name, col || '#22c55e', right, sub, c.p.name);
    }
    function wrap(icon, title, out, cls, emptyMsg) {
      out.sort(function (a, b) { return b.sc - a.sc; });
      var col = (cls === 'c-tough' || cls === 'c-red') ? '#ef4444' : (cls === 'c-fav' ? '#eab308' : '#22c55e');
      // defer empties to degWrap: hidden in the focused fixture, standard empty-state in the Degen Crew tab
      return degWrap(icon, title, out.length ? out.slice(0, 12).map(function (c) { return card(c, col); }) : [], cls);
    }
    var d3 = function (v) { return v.toFixed(3).replace(/^0/, ''); };
    // most-recent-first gamelog for a batter (sorted by date desc when present)
    function recentLogs(name) {
      var lg = (logsFor(name) || []).slice();
      lg.sort(function (a, b) { var da = a.Date || a.date || a.gameDate || '', db = b.Date || b.date || b.gameDate || ''; return db < da ? -1 : db > da ? 1 : 0; });
      return lg;
    }

    function hot() {
      var out = [];
      slateBats().forEach(function (p) {
        var h1 = JS.getHitRate(p.name, 'H', 0.5, false);
        var recent = JS.getRecentAvg(p.name, 'H', 7), season = (p.matches ? (p.H || 0) / p.matches : 0);
        if (!h1 || h1.rate < 0.72 || recent == null || recent <= season * 1.05) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: h1.rate, mkt: 'H', headline: '1+ Hit',
          detail: 'hit ' + Math.round(h1.rate * 100) + '% (' + h1.n + ') \u00b7 L7 ' + recent.toFixed(2) + ' vs season ' + season.toFixed(2) });
      });
      return wrap('ti-flame', 'Hot Bats', out, 'c-soft', 'No hitters on the slate are running hot right now.');
    }

    // Cold Bats — pure form fade (consecutive hitless games)
    function cold() {
      var out = [];
      slateBats().forEach(function (p) {
        var lg = recentLogs(p.name); if (lg.length < 5) return;
        var n = 0; for (var i = 0; i < lg.length; i++) { if ((+(lg[i].H || 0)) < 1) n++; else break; }
        if (n < 4) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: n, mkt: 'H', headline: n + '-game skid',
          detail: 'hitless in last ' + n + ' games' + (p.AVG != null ? ' \u00b7 ' + d3(p.AVG) + ' AVG' : '') });
      });
      return wrap('ti-snowflake', 'Cold Bats', out, 'c-tough', 'No notable hitless skids on the slate.');
    }

    // Whiff Risk — batter likely to strike out into a high-K arm
    function whiff() {
      var out = [];
      slateBats().forEach(function (p) {
        var pit = JS.starterFacing(p); if (!pit) return;
        var soPerG = p.matches ? (p.SO || 0) / p.matches : 0;
        if (!((pit.K9 || 0) >= 8.8 || soPerG >= 1.1)) return;
        var soRate = (p.PA ? (p.SO || 0) / p.PA : soPerG / 4.2);       // K per PA
        var projK = soRate * 4.2 * ((pit.K9 || 8.6) / 8.6);            // adjust for the arm
        var prob = 1 - Math.exp(-Math.max(0.01, projK));               // P(1+ K)
        if (prob < 0.66) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: prob, mkt: 'K', headline: '1+ K',
          detail: Math.round(prob * 100) + '% to strike out \u00b7 ' + soPerG.toFixed(2) + ' K/g vs ' + esc(pit.name) + ' (K/9 ' + (pit.K9 || 0).toFixed(1) + ')' });
      });
      return wrap('ti-circle-x', 'Whiff Risk', out, 'c-tough', 'No standout strikeout-risk bats on the slate.');
    }

    function ktargets() {
      var out = [], teams = _fixtureSet();
      (JS.probableNames ? JS.probableNames() : []).forEach(function (nm) {
        var p = byName(nm); if (!p || p.role !== 'pitch') return;
        if (!teams.has(p.team)) return;   // scope to the fixture set (focused game or full slate)
        var kp = JS.pitcherKProj(p);
        if (kp.proj < kp.line + 0.3) return;
        out.push({ p: p, opp: kp.opp, sc: kp.proj - kp.line, mkt: 'K', headline: kp.line + '+ Ks',
          detail: 'proj ' + kp.proj.toFixed(1) + ' \u00b7 K/9 ' + kp.k9.toFixed(1) + ' \u00b7 vs ' + (kp.opp || '?') });
      });
      return wrap('ti-ball-baseball', 'Strike Time', out, 'c-fav', 'No standout strikeout spots on the slate.');
    }

    function longball() {
      var out = [];
      slateBats().forEach(function (p) {
        var r = JS.projBat(p, 'HR'), pk = JS.parkFor(p.team);
        var prob = JS.batProb(p, 'HR', 0.5);
        if (prob < 0.14) return;
        var pitTag = (r.pit && r.pit.HR9 >= 1.3) ? ' \u00b7 HR-prone arm' : '';
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: prob, mkt: 'HR', headline: 'Home Run',
          detail: Math.round(prob * 100) + '% to go yard \u00b7 park HR ' + (pk.hrFactor || 1).toFixed(2) + 'x' + pitTag });
      });
      return wrap('ti-bolt', 'Homers', out, 'c-soft', 'No standout home-run spots on the slate.');
    }

    function platoon() {
      var out = [];
      slateBats().forEach(function (p) {
        var pit = JS.starterFacing(p); if (!pit) return;
        var sp = pit.throws === 'L' ? p.splitVsL : p.splitVsR;
        if (!sp || !p.SLG || !sp.SLG || (sp.PA || 0) < 25) return;
        var ratio = sp.SLG / p.SLG; if (ratio < 1.15) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: ratio, mkt: 'H', headline: 'vs ' + pit.throws + 'HP',
          detail: 'SLG ' + d3(sp.SLG) + ' vs ' + pit.throws + 'HP (season ' + d3(p.SLG) + ') \u00b7 +' + Math.round((ratio - 1) * 100) + '%' });
      });
      return wrap('ti-arrows-left-right', 'Sluggers', out, 'c-fav', 'No big handedness mismatches on the slate.');
    }

    function coldarm() {
      var out = [];
      slateBats().forEach(function (p) {
        var pit = JS.starterFacing(p); if (!pit) return;
        var sp = p.bats === 'L' ? pit.splitVsL : pit.splitVsR;
        var oppAVG = (sp && sp.oppAVG != null) ? sp.oppAVG : pit.oppAVG;
        if (oppAVG == null || oppAVG < 0.265) return;
        var h1 = JS.getHitRate(p.name, 'H', 0.5, false); if (!h1 || h1.rate < 0.6) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: oppAVG, mkt: 'H', headline: '1+ Hit',
          detail: 'vs ' + esc(pit.name) + ' (oppAVG ' + d3(oppAVG) + ', K/9 ' + (pit.K9 || 0).toFixed(1) + ') \u00b7 hit ' + Math.round(h1.rate * 100) + '%' });
      });
      return wrap('ti-temperature', 'Hittable Arms', out, 'c-soft', 'No standout contact spots on the slate.');
    }

    // Table Setters — top-of-order run scorers vs a walk-prone starter
    function runners() {
      var out = [];
      slateBats().forEach(function (p) {
        if ((p.order || 9) > 3) return;
        var pit = JS.starterFacing(p); if (!pit) return;
        if (!((pit.WHIP || 0) >= 1.25 || (pit.BB9 || 0) >= 3.3)) return;
        var prob = JS.batProb(p, 'R', 0.5); if (prob == null || prob < 0.50) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: prob, mkt: 'R', headline: '1+ Run',
          detail: Math.round(prob * 100) + '% to score \u00b7 bats ' + (p.order || '?') + ' vs ' + esc(pit.name) + ' (WHIP ' + (pit.WHIP || 0).toFixed(2) + ', BB/9 ' + (pit.BB9 || 0).toFixed(1) + ')' });
      });
      return wrap('ti-arrow-up-right', 'Runners', out, 'c-soft', 'No standout run-scorer spots on the slate.');
    }

    // Bunnies / Bogeys — batter's history vs today's starter (bvp)
    function bvpTile(dir) {
      var out = [];
      slateBats().forEach(function (p) {
        var pit = JS.starterFacing(p); if (!pit || !p.bvp) return;
        var v = p.bvp[pit.id] || p.bvp[String(pit.id)]; if (!v || (v.PA || 0) < 10) return;
        if (dir === 'bunny' && v.AVG < 0.350) return;
        if (dir === 'bogey' && v.AVG > 0.150) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: dir === 'bunny' ? v.AVG : (1 - v.AVG),
          mkt: 'H', headline: dir === 'bunny' ? 'Owns the arm' : 'Owned by the arm',
          detail: d3(v.AVG) + ' (' + (v.H || 0) + '/' + v.PA + ', ' + (v.HR || 0) + ' HR) vs ' + esc(pit.name) });
      });
      return dir === 'bunny'
        ? wrap('ti-mood-happy', 'Bunnies', out, 'c-soft', 'No batters with a strong record vs their starter today.')
        : wrap('ti-mood-sad', 'Bogeys', out, 'c-tough', 'No batters who struggle vs their starter today.');
    }
    function bunny() { return bvpTile('bunny'); }
    function bogey() { return bvpTile('bogey'); }

    function wheels() {
      var out = [];
      slateBats().forEach(function (p) {
        var sbRate = p.matches ? (p.SB || 0) / p.matches : 0; if (sbRate < 0.12) return;
        var sb1 = JS.getHitRate(p.name, 'SB', 0.5, false);
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: sbRate, mkt: 'SB', headline: 'Stolen Base',
          detail: (p.SB || 0) + ' SB in ' + p.matches + ' G (' + sbRate.toFixed(2) + '/G)' + (sb1 ? ' \u00b7 1+ SB ' + Math.round(sb1.rate * 100) + '%' : '') });
      });
      return wrap('ti-run', 'Sneaky Buggers', out, 'c-fav', 'No standout steal threats on the slate.');
    }

    // Statcast — Due (unlucky, back) & Running Hot (overperforming, fade). Dark until the data build carries statcast.
    function statcastGap(p) { var s = p.statcast; if (!s || s.xwoba == null || s.woba == null) return null; return s.xwoba - s.woba; }
    function hasStatcast() { return slateBats().some(function (p) { return p.statcast && p.statcast.xwoba != null; }); }
    function due() {
      if (!hasStatcast()) return wrap('ti-trending-up', 'Due / Unlucky', [], 'c-soft', 'Statcast lands with the next data build.');
      var out = [];
      slateBats().forEach(function (p) {
        var gap = statcastGap(p), s = p.statcast;
        if (gap == null || gap < 0.028 || !s || s.xwoba < 0.330) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: gap, mkt: 'H', headline: 'Due',
          detail: '+' + (gap * 1000).toFixed(0) + ' xwOBA gap (unlucky) \u00b7 ' + d3(s.xwoba) + ' xwOBA' + (s.barrelPct != null ? ' \u00b7 ' + s.barrelPct + '% barrel' : '') });
      });
      return wrap('ti-trending-up', 'Due / Unlucky', out, 'c-soft', 'No standout bounce-back candidates on the slate.');
    }
    function hothand() {
      if (!hasStatcast()) return wrap('ti-trending-down', 'Running Hot', [], 'c-tough', 'Statcast lands with the next data build.');
      var out = [];
      slateBats().forEach(function (p) {
        var gap = statcastGap(p); if (gap == null || gap > -0.032) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: -gap, mkt: 'H', headline: 'Running Hot',
          detail: (gap * 1000).toFixed(0) + ' xwOBA gap (overperforming \u2014 fade) \u00b7 ' + d3(p.statcast.xwoba) + ' xwOBA' });
      });
      return wrap('ti-trending-down', 'Running Hot', out, 'c-tough', 'No standout fade candidates on the slate.');
    }

    // matchup score approximating the standalone's mu: weak arm + platoon edge + park
    function _muScore(p) {
      var pit = JS.starterFacing(p); if (!pit) return { score: 0, pit: null };
      var sc = 0;
      if (pit.ERA != null) sc += (pit.ERA - 4.0) * 6;
      if (pit.WHIP != null) sc += (pit.WHIP - 1.25) * 40;
      if (pit.K9 != null) sc -= (pit.K9 - 8.5) * 3;
      var sp = pit.throws === 'L' ? p.splitVsL : p.splitVsR;
      if (sp && sp.SLG && p.SLG) sc += (sp.SLG / p.SLG - 1) * 60;
      var pk = JS.parkFor(p.team); if (pk && pk.hrFactor) sc += (pk.hrFactor - 1) * 30;
      return { score: sc, pit: pit };
    }
    function greenlights() {
      var out = [];
      slateBats().forEach(function (p) {
        var mu = _muScore(p); if (!mu.pit) return;
        var prob = JS.batProb(p, 'H', 0.5);
        if (prob == null || prob < 0.55 || mu.score < 8) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: prob * 100 + mu.score, mkt: 'H', headline: '1+ Hit',
          detail: Math.round(prob * 100) + '% \u00b7 strong spot vs ' + esc(mu.pit.name) + ' (ERA ' + (mu.pit.ERA || 0).toFixed(2) + ', WHIP ' + (mu.pit.WHIP || 0).toFixed(2) + ')' });
      });
      return wrap('ti-circle-check', 'Green Lights', out, 'c-soft', 'No standout green-light spots on the slate.');
    }
    function deathriders() {
      var out = [];
      slateBats().forEach(function (p) {
        var mu = _muScore(p); if (!mu.pit) return;
        var prob = JS.batProb(p, 'H', 0.5);
        if (prob == null || prob > 0.45 || mu.score > -8) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: -(prob * 100 + mu.score), mkt: 'H', headline: 'Fade',
          detail: Math.round(prob * 100) + '% to hit \u00b7 tough vs ' + esc(mu.pit.name) + ' (K/9 ' + (mu.pit.K9 || 0).toFixed(1) + ', ERA ' + (mu.pit.ERA || 0).toFixed(2) + ')' });
      });
      return wrap('ti-skull', 'Death Riders', out, 'c-tough', 'No standout fade spots on the slate.');
    }
    function streakers() {
      var out = [];
      slateBats().forEach(function (p) {
        var lg = recentLogs(p.name); if (lg.length < 5) return;
        var hs = 0; for (var i = 0; i < lg.length; i++) { if ((+(lg[i].H || 0)) >= 1) hs++; else break; }
        var ts = 0; for (var j = 0; j < lg.length; j++) { if ((+(lg[j].TB || 0)) >= 2) ts++; else break; }
        if (hs >= 5) out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: hs, mkt: 'H', headline: hs + '-game hit streak', detail: '1+ hit in ' + hs + ' straight' });
        else if (ts >= 5) out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: ts, mkt: 'TB', headline: ts + '-game 2+ TB', detail: '2+ bases in ' + ts + ' straight' });
      });
      return wrap('ti-flame', 'Streakers', out, 'c-soft', 'No hot streaks on the slate.');
    }

    // RBI Men — run-producing bats (batter_rbis)
    function rbimen() {
      var out = [];
      slateBats().forEach(function (p) {
        if ((p.order || 9) > 6) return;
        var prob = JS.batProb(p, 'RBI', 0.5); if (prob == null || prob < 0.42) return;
        var mu = _muScore(p);
        var rbiRate = p.matches ? (p.RBI || 0) / p.matches : 0;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: prob * 100 + (mu.score || 0) * 0.2, mkt: 'RBI', headline: '1+ RBI',
          detail: Math.round(prob * 100) + '% \u00b7 ' + rbiRate.toFixed(2) + ' RBI/g \u00b7 bats ' + (p.order || '?') + (mu.pit ? ' vs ' + esc(mu.pit.name) : '') });
      });
      return wrap('ti-target', 'RBI Men', out, 'c-soft', 'No standout RBI spots on the slate.');
    }
    // Free Passes — patient hitters vs wild arms (batter_walks)
    function freepasses() {
      var out = [];
      slateBats().forEach(function (p) {
        var pit = JS.starterFacing(p); if (!pit) return;
        var bbRate = p.PA ? (p.BB || 0) / p.PA : 0;
        if (bbRate < 0.09) return;
        if (!((pit.BB9 || 0) >= 3.3 || (pit.WHIP || 0) >= 1.30)) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: bbRate * 100 + (pit.BB9 || 0), mkt: 'BB', headline: '1+ Walk',
          detail: Math.round(bbRate * 100) + '% BB rate vs ' + esc(pit.name) + ' (BB/9 ' + (pit.BB9 || 0).toFixed(1) + ', WHIP ' + (pit.WHIP || 0).toFixed(2) + ')' });
      });
      return wrap('ti-walk', 'Free Passes', out, 'c-soft', 'No standout walk spots on the slate.');
    }
    // Grinders — starters in tough spots, likely to allow runs (pitcher_earned_runs) — a fade
    function grinders() {
      var out = [], teams = _fixtureSet();
      (JS.probableNames ? JS.probableNames() : []).forEach(function (nm) {
        var p = byName(nm); if (!p || p.role !== 'pitch') return;
        if (!teams.has(p.team)) return;
        var pk = JS.parkFor(p.team), score = 0;
        if (p.ERA != null) score += (p.ERA - 4.0) * 8;
        if (p.WHIP != null) score += (p.WHIP - 1.25) * 30;
        if (p.HR9 != null) score += (p.HR9 - 1.1) * 15;
        if (p.oppAVG != null) score += (p.oppAVG - 0.250) * 120;
        if (pk && pk.runFactor) score += (pk.runFactor - 1) * 40;
        if (score < 8) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: score, mkt: 'ER', headline: 'Runs allowed',
          detail: 'ERA ' + (p.ERA || 0).toFixed(2) + ' \u00b7 WHIP ' + (p.WHIP || 0).toFixed(2) + (p.oppAVG != null ? ' \u00b7 oppAVG ' + d3(p.oppAVG) : '') + (pk ? ' \u00b7 park runs ' + (pk.runFactor || 1).toFixed(2) + 'x' : '') });
      });
      return wrap('ti-flame', 'Grinders', out, 'c-tough', 'No standout pitcher-fade spots on the slate.');
    }
    // Innings Eaters — efficient starters who go deep (pitcher_outs)
    function inningseaters() {
      var out = [], teams = _fixtureSet();
      (JS.probableNames ? JS.probableNames() : []).forEach(function (nm) {
        var p = byName(nm); if (!p || p.role !== 'pitch') return;
        if (!teams.has(p.team)) return;
        var ipg = (p.GS && p.IP) ? p.IP / p.GS : null;
        if (ipg == null || ipg < 5.3 || (p.WHIP || 99) > 1.28) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: ipg, mkt: 'OUTS', headline: Math.round(ipg * 3) + '+ Outs',
          detail: ipg.toFixed(1) + ' IP/start \u00b7 WHIP ' + (p.WHIP || 0).toFixed(2) + ' \u00b7 K/9 ' + (p.K9 || 0).toFixed(1) });
      });
      return wrap('ti-clock-hour-9', 'Innings Eaters', out, 'c-fav', 'No standout deep-start pitchers on the slate.');
    }

    var tiles = { greenlights: greenlights, platoon: platoon, runners: runners, rbimen: rbimen,
      freepasses: freepasses, grinders: grinders, inningseaters: inningseaters, due: due,
      ktargets: ktargets, longball: longball, wheels: wheels, streakers: streakers,
      bunny: bunny, whiff: whiff, hothand: hothand, cold: cold, deathriders: deathriders,
      bogey: bogey, hot: hot, coldarm: coldarm };

    function playStyles(p, pg) { return [pg]; }
    function collectOU() { return []; }
    function matchupLegs() { return []; }
    function captureOU() { return null; }
    function captureMatchup() { return null; }

    return { tiles: tiles, playStyles: playStyles, collectOU: collectOU,
      matchupLegs: matchupLegs, captureOU: captureOU, captureMatchup: captureMatchup };
  }
  return { create: create };
})();
