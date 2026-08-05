/* JTT MLB — signals.js  (window.JTTSignals)
   Degen tiles for the unified shell, all driven by mlb/scoring.js (batter-vs-pitcher engine).
   Instantiated via JTTSignals.create(deps). Tiles operate on today's slate only.
   Six engines: Hot Bats, K Targets, Long Ball, Platoon Edge, Hittable Arms, Wheels. */
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
    var players = deps.players || [];
    var JS = deps.JTTScoring || window.JTTScoring;

    function slateBats() {
      var teams = _fixtureSet();
      return (players || []).filter(function (p) { return p.role === 'bat' && teams.has(p.team) && (p.matches || 0) >= 15; });
    }
    function card(c) {
      var q = esc(c.p.name).replace(/'/g, "\\'");
      return '<div class="lc-card" onclick="openPlayer(\'' + q + '\')">' +
        '<div class="lc-hd"><span class="lc-nm">' + esc(c.p.name) + '</span>' + _degBadges(c.p.name) +
        '<span class="lc-meta">' + posShort(c.p.position) + ' \u00b7 ' + abbr(c.p.team) + (c.opp ? ' v ' + abbr(c.opp) : '') + '</span></div>' +
        '<div class="tp-body-meta" style="border:0;padding:2px 0 6px"><b>' + c.headline + '</b> \u00b7 ' + c.detail + '</div></div>';
    }
    function wrap(icon, title, out, cls, emptyMsg) {
      out.sort(function (a, b) { return b.sc - a.sc; });
      if (!out.length) return emptyState(icon, title, emptyMsg);
      return degWrap(icon, title, out.slice(0, 12).map(card), cls);
    }
    var d3 = function (v) { return v.toFixed(3).replace(/^0/, ''); };

    function hot() {
      var out = [];
      slateBats().forEach(function (p) {
        var h1 = JS.getHitRate(p.name, 'H', 0.5, false);
        var recent = JS.getRecentAvg(p.name, 'H', 7), season = (p.matches ? (p.H || 0) / p.matches : 0);
        if (!h1 || h1.rate < 0.72 || recent == null || recent <= season * 1.05) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: h1.rate, headline: '1+ Hit',
          detail: 'hit ' + Math.round(h1.rate * 100) + '% (' + h1.n + ') \u00b7 L7 ' + recent.toFixed(2) + ' vs season ' + season.toFixed(2) });
      });
      return wrap('ti-flame', 'Hot Bats', out, 'c-soft', 'No hitters on the slate are running hot right now.');
    }

    function ktargets() {
      var out = [];
      (JS.probableNames ? JS.probableNames() : []).forEach(function (nm) {
        var p = byName(nm); if (!p || p.role !== 'pitch') return;
        var kp = JS.pitcherKProj(p);
        if (kp.proj < kp.line + 0.3) return;
        out.push({ p: p, opp: kp.opp, sc: kp.proj - kp.line, headline: kp.line + '+ Ks',
          detail: 'proj ' + kp.proj.toFixed(1) + ' \u00b7 K/9 ' + kp.k9.toFixed(1) + ' \u00b7 vs ' + (kp.opp || '?') });
      });
      return wrap('ti-target-arrow', 'K Targets', out, 'c-fav', 'No standout strikeout spots on the slate.');
    }

    function longball() {
      var out = [];
      slateBats().forEach(function (p) {
        var r = JS.projBat(p, 'HR'), pk = JS.parkFor(p.team);
        var prob = JS.batProb(p, 'HR', 0.5);           // P(1+ HR)
        if (prob < 0.14) return;                        // ~1-in-7 or better
        var pitTag = (r.pit && r.pit.HR9 >= 1.3) ? ' \u00b7 HR-prone arm' : '';
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: prob, headline: 'Home Run',
          detail: Math.round(prob * 100) + '% to go yard \u00b7 park HR ' + (pk.hrFactor || 1).toFixed(2) + 'x' + pitTag });
      });
      return wrap('ti-ball-baseball', 'Long Ball', out, 'c-soft', 'No standout home-run spots on the slate.');
    }

    function platoon() {
      var out = [];
      slateBats().forEach(function (p) {
        var pit = JS.starterFacing(p); if (!pit) return;
        var sp = pit.throws === 'L' ? p.splitVsL : p.splitVsR;
        if (!sp || !p.SLG || !sp.SLG || (sp.PA || 0) < 25) return;   // guard vs tiny-sample splits
        var ratio = sp.SLG / p.SLG; if (ratio < 1.15) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: ratio, headline: 'vs ' + pit.throws + 'HP',
          detail: 'SLG ' + d3(sp.SLG) + ' vs ' + pit.throws + 'HP (season ' + d3(p.SLG) + ') \u00b7 +' + Math.round((ratio - 1) * 100) + '%' });
      });
      return wrap('ti-arrows-shuffle', 'Platoon Edge', out, 'c-fav', 'No big handedness mismatches on the slate.');
    }

    function coldarm() {
      var out = [];
      slateBats().forEach(function (p) {
        var pit = JS.starterFacing(p); if (!pit) return;
        var sp = p.bats === 'L' ? pit.splitVsL : pit.splitVsR;
        var oppAVG = (sp && sp.oppAVG != null) ? sp.oppAVG : pit.oppAVG;
        if (oppAVG == null || oppAVG < 0.265) return;
        var h1 = JS.getHitRate(p.name, 'H', 0.5, false); if (!h1 || h1.rate < 0.6) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: oppAVG, headline: '1+ Hit',
          detail: 'vs ' + esc(pit.name) + ' (oppAVG ' + d3(oppAVG) + ', K/9 ' + (pit.K9 || 0).toFixed(1) + ') \u00b7 hit ' + Math.round(h1.rate * 100) + '%' });
      });
      return wrap('ti-temperature', 'Hittable Arms', out, 'c-soft', 'No standout contact spots on the slate.');
    }

    function wheels() {
      var out = [];
      slateBats().forEach(function (p) {
        var sbRate = p.matches ? (p.SB || 0) / p.matches : 0; if (sbRate < 0.12) return;
        var sb1 = JS.getHitRate(p.name, 'SB', 0.5, false);
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: sbRate, headline: 'Stolen Base',
          detail: (p.SB || 0) + ' SB in ' + p.matches + ' G (' + sbRate.toFixed(2) + '/G)' + (sb1 ? ' \u00b7 1+ SB ' + Math.round(sb1.rate * 100) + '%' : '') });
      });
      return wrap('ti-run', 'Wheels', out, 'c-fav', 'No standout steal threats on the slate.');
    }

    var tiles = { hot: hot, ktargets: ktargets, longball: longball, platoon: platoon, coldarm: coldarm, wheels: wheels };

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
