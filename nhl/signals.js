/* JTT NHL signals module — basketball Degen engines in the shell's JTTSignals.create() format.
   Ported from the standalone NBA tool onto the shell signal contract
   { tiles, playStyles, collectOU, matchupLegs, captureOU, captureMatchup }.
   Odds tiles soft-gate: a signal shows its metric until the NBL odds worker prices it. */
window.JTTSignals = (function () {
  "use strict";

  function create(deps) {
    deps = deps || {};
    var JS         = deps.JTTScoring || window.JTTScoring;
    var players    = deps.players || [];
    var oddsFor    = deps.oddsFor || function () { return null; };
    var nextOpp    = deps.nextOpp || function () { return null; };
    var playersOnTeam = deps.playersOnTeam || function () { return []; };
    var logsFor    = deps.logsFor || function () { return []; };
    var abbr       = deps.abbr || function (t) { return t; };
    var esc        = deps.esc || function (s) { return s == null ? '' : String(s); };
    var posShort   = deps.posShort || function (p) { return p || ''; };
    var degRow     = deps.degRow || function () { return ''; };
    var degWrap    = deps.degWrap || function () { return ''; };
    var SIGNAL     = deps.SIGNAL_MIN_ODDS || deps.SIGNAL || { over: 1.60, under: 1.60 };
    var teamMap    = deps.teamMap || {};
    var _fixtureSet = deps._fixtureSet || function () { return { has: function () { return true; } }; };
    var MIN_GAMES = 5;

    function slate() {
      var fs = _fixtureSet();
      return (players || []).filter(function (p) { return fs.has(p.team) && p.position !== 'G' && (p.games || p.matches || 0) >= MIN_GAMES; });
    }
    function teamsOnSlate() {
      var fs = _fixtureSet(), set = {};
      (players || []).forEach(function (p) { if (fs.has(p.team)) set[p.team] = 1; });
      return Object.keys(set);
    }
    function recentLogs(name) {
      var lg = (logsFor(name) || []).slice();
      lg.sort(function (a, b) { var da = a.Date || a.date || '', db = b.Date || b.date || ''; return da < db ? 1 : da > db ? -1 : 0; });
      return lg;
    }
    function dvpGroup(p) { var g = p.pos5 || p.position || 'F'; var m = JS && JS.POS_TO_DVP; return (m && typeof m === 'object' && m[g]) || g; }

    // ---- render helpers (soft odds-gate: show price if available, else the signal metric) ----
    function card(c, col) {
      var o = oddsFor(c.p.name, c.mkt), side = c.side || 'over';
      var priceTxt = (o && o[side] != null) ? ('$' + (+o[side]).toFixed(2) + (o.book ? ' ' + esc(o.book) : '')) : (c.headline || '');
      var sub = posShort(c.p.position) + ' \u00b7 ' + abbr(c.p.team) + (c.opp ? ' v ' + abbr(c.opp) : '') + (c.detail ? ' \u00b7 ' + c.detail : '');
      return degRow(c.p.name, col || '#22c55e', priceTxt, sub, c.p.name);
    }
    function wrap(icon, title, out, cls, emptyMsg) {
      out = (out || []).slice().sort(function (a, b) { return b.sc - a.sc; });
      var col = (cls === 'c-tough' || cls === 'c-red') ? '#ef4444' : (cls === 'c-fav' ? '#eab308' : '#22c55e');
      return degWrap(icon, title, out.length ? out.slice(0, 12).map(function (c) { return card(c, col); }) : [], cls);
    }

    // ---- specialists: top players at a stat facing a soft defence (dvpRanked / getDVPPct) ----
    function specialist(stat, icon, title) {
      var out = [];
      slate().forEach(function (p) {
        var opp = nextOpp(p.team); if (!opp) return;
        var avg = p[stat]; if (avg == null || avg <= 0) return;
        var pct = JS && JS.getDVPPct ? JS.getDVPPct(opp, dvpGroup(p), stat) : null;
        if (pct == null || pct < 4) return;           // opp must allow 4%+ over league avg to that position
        out.push({ p: p, opp: opp, mkt: stat, sc: avg * (1 + pct / 100),
          headline: avg.toFixed(1) + ' ' + stat + '/g', detail: 'v ' + abbr(opp) + ' soft +' + Math.round(pct) + '%' });
      });
      return wrap(icon, title, out, 'c-soft', 'No soft matchups on the slate.');
    }

    // ---- streakers: current run clearing a meaningful line ----
    function streak() {
      var out = [];
      slate().forEach(function (p) {
        var lg = recentLogs(p.name); if (lg.length < 5) return;
        var s = 0; for (var i = 0; i < lg.length; i++) { if ((+(lg[i].points || 0)) >= 1) s++; else break; }
        var sh = 0; for (var j = 0; j < lg.length; j++) { if ((+(lg[j].shots || 0)) >= 3) sh++; else break; }
        if (s >= 4) out.push({ p: p, opp: nextOpp(p.team), mkt: 'points', sc: s, headline: s + '-game point streak', detail: 'hot' });
        else if (sh >= 4) out.push({ p: p, opp: nextOpp(p.team), mkt: 'shots', sc: sh, headline: sh + '-game 3+ SOG', detail: 'volume' });
      });
      return wrap('ti-flame', 'Streakers', out, 'c-soft', 'No hot streaks on the slate.');
    }

    // ---- form alerts: L5 well above season ----
    function form() {
      var out = [];
      slate().forEach(function (p) {
        var l5 = JS && JS.getRecentAvg ? JS.getRecentAvg(p.name, 'points', 5) : null, season = p.points;
        if (l5 == null || season == null || season < 0.4) return;
        var delta = (l5 - season) / season * 100;
        if (delta >= 18) out.push({ p: p, opp: nextOpp(p.team), mkt: 'points', sc: delta,
          headline: 'L5 ' + l5.toFixed(2) + ' pts', detail: '+' + Math.round(delta) + '% vs season' });
      });
      return wrap('ti-trending-up', 'Form Alerts', out, 'c-soft', 'No trend shifts on the slate.');
    }

    // ---- bunnies / bogey: H2H vs the next opponent ----
    function h2hEngine(good) {
      var out = [];
      slate().forEach(function (p) {
        var opp = nextOpp(p.team); if (!opp) return;
        var vs = recentLogs(p.name).filter(function (r) { return r.Opp === opp; });
        if (vs.length < 3) return;
        var avgVs = vs.reduce(function (s, r) { return s + (+(r.points || 0)); }, 0) / vs.length;
        var base = p.points; if (base == null || base < 8) return;
        var delta = (avgVs - base) / base * 100;
        if (good && delta >= 15) out.push({ p: p, opp: opp, mkt: 'points', sc: delta,
          headline: avgVs.toFixed(1) + ' pts v ' + abbr(opp), detail: '+' + Math.round(delta) + '% (' + vs.length + ' mtgs)' });
        if (!good && delta <= -15) out.push({ p: p, opp: opp, mkt: 'points', sc: -delta, side: 'under',
          headline: avgVs.toFixed(1) + ' pts v ' + abbr(opp), detail: Math.round(delta) + '% (' + vs.length + ' mtgs)' });
      });
      return good ? wrap('ti-carrot', 'Bunnies', out, 'c-soft', 'No favourable H2H on the slate.')
                  : wrap('ti-mood-sad', 'Bogey', out, 'c-red', 'No bogey matchups on the slate.');
    }

    // ---- power play: PP-point threats vs the next opponent ----
    function pp() {
      var out = [];
      slate().forEach(function (p) {
        if ((p.ppPoints || 0) < 0.3) return; var opp = nextOpp(p.team); if (!opp) return;
        out.push({ p: p, opp: opp, mkt: 'ppPoints', sc: p.ppPoints,
          headline: (p.ppPoints).toFixed(2) + ' PPP/g', detail: 'PP threat' });
      });
      return wrap('ti-bolt', 'Power Play', out, 'c-soft', 'No power-play threats on the slate.');
    }

    // ---- usage trend: minutes climbing vs season ----
    function usage() {
      var out = [];
      slate().forEach(function (p) {
        var l5 = JS && JS.getRecentAvg ? JS.getRecentAvg(p.name, 'toiMin', 5) : null, season = p.toiMin;
        if (l5 == null || season == null || season < 12) return;
        var delta = (l5 - season) / season * 100;
        if (delta >= 12) out.push({ p: p, opp: nextOpp(p.team), mkt: 'points', sc: delta,
          headline: 'L5 ' + l5.toFixed(1) + ' TOI', detail: '+' + Math.round(delta) + '% minutes' });
      });
      return wrap('ti-activity', 'Usage Trend', out, 'c-soft', 'No role shifts on the slate.');
    }

    // ---- Green Lights / Death Riders — strongest verdict vs the posted line (needs odds) ----
    function collectOU(teamList, kind) {
      if (!JS) return [];
      var side = kind === 'over' ? 'over' : 'under';
      var minOdds = (SIGNAL || {})[side];
      var GREEN = 4.5, DEATH = -8.0, byPlayer = {};   // hockey-calibrated (was 5.5/-3.5 basketball)
      var MARKETS = [['shots', 'Shots'], ['points', 'Points'], ['goals', 'Goals'],
                     ['assists', 'Assists'], ['ppPoints', 'PP Points']];
      (teamList || []).forEach(function (team) {
        var opp = nextOpp(team); if (!opp) return;
        (playersOnTeam(team) || []).forEach(function (p) {
          MARKETS.forEach(function (mk) {
            var od = oddsFor(p.name, mk[0]); if (!od || od.line == null) return;
            var price = od[side]; if (price == null) return;
            if (minOdds != null && price < minOdds) return;
            var v = JS.verdict(p, mk[0], od.line, opp); if (!v) return;
            var sc = v.score, strong = kind === 'over' ? sc >= GREEN : sc <= DEATH;
            if (!strong) return;
            var ex = byPlayer[p.name];
            if (ex && (kind === 'over' ? ex.score >= sc : ex.score <= sc)) return;
            var hr = JS.getHitRate(p.name, mk[0], od.line, false); if (!hr) return;
            byPlayer[p.name] = { p: p, market: mk[0], mktLabel: mk[1], line: od.line, odds: price, book: od.book,
              score: sc, verdictCol: v.col, verdictLabel: v.label,
              l5: JS.getRecentAvg(p.name, mk[0], 5) || 0, hitRate: hr.rate, games: hr.n, team: team, oppName: opp };
          });
        });
      });
      return Object.keys(byPlayer).map(function (k) { return byPlayer[k]; })
        .sort(function (a, b) { return kind === 'over' ? b.score - a.score : a.score - b.score; });
    }
    function ouTile(kind) {
      var rows = collectOU(teamsOnSlate(), kind).map(function (r) {
        return { p: r.p, opp: r.oppName, mkt: r.market, side: kind, sc: Math.abs(r.score),
          headline: (kind === 'over' ? 'OVER ' : 'UNDER ') + r.line + ' ' + r.mktLabel,
          detail: Math.round(r.hitRate * 100) + '% hit \u00b7 ' + r.mktLabel };
      });
      return kind === 'over' ? wrap('ti-traffic-lights', 'Green Lights', rows, 'c-soft', 'No green lights — odds pending.')
                             : wrap('ti-skull', 'Death Riders', rows, 'c-red', 'No death riders — odds pending.');
    }

    // ---- Multi Builder legs: strongest priced player-market plays for a team pair ----
    function matchupLegs(teamPlayers, team, opp) {
      var legs = [];
      (teamPlayers || []).forEach(function (p) {
        ['points', 'rebounds', 'assists', 'threes', 'pra'].forEach(function (mk) {
          var od = oddsFor(p.name, mk); if (!od || od.line == null || od.over == null) return;
          var v = JS ? JS.verdict(p, mk, od.line, opp) : null; if (!v || v.score < 3) return;
          legs.push({ p: p, market: mk, line: od.line, over: od.over, book: od.book, score: v.score, verdictCol: v.col });
        });
      });
      return legs.sort(function (a, b) { return b.score - a.score; });
    }

    // ---- Goalie Watch: goalies facing high-shot opponents project for more saves ----
    function goalies() {
      var fs = _fixtureSet(), out = [];
      (players || []).filter(function (p) { return p.position === 'G' && fs.has(p.team) && (p.games || p.matches || 0) >= 5; })
        .forEach(function (p) {
          var opp = nextOpp(p.team); if (!opp) return;
          var oppSF = ((teamMap || {})[opp] || {}).shotsFor;   // opp shots-for/g = volume this goalie faces
          var sv = p.saves || 0;
          out.push({ p: p, opp: opp, mkt: 'saves', side: 'over', sc: sv + ((oppSF || 0) * 0.4),
            headline: sv.toFixed(1) + ' saves/g',
            detail: 'v ' + abbr(opp) + (oppSF ? ' \u00b7 ' + oppSF.toFixed(0) + ' SOG/g faced' : '') });
        });
      return wrap('ti-shield-half', 'Goalie Watch', out, 'c-soft', 'No starting goalies on the slate.');
    }

    var tiles = {
      green: function () { return ouTile('over'); },
      death: function () { return ouTile('under'); },
      snipers: function () { return specialist('shots', 'ti-target-arrow', 'Snipers'); },
      producers: function () { return specialist('points', 'ti-chart-line', 'Producers'); },
      playmakers: function () { return specialist('assists', 'ti-hand-finger', 'Playmakers'); },
      finishers: function () { return specialist('goals', 'ti-ball-hockey', 'Finishers'); },
      bunnies: function () { return h2hEngine(true); },
      bogey: function () { return h2hEngine(false); },
      streak: streak, form: form, pp: pp, usage: usage, goalies: goalies
    };

    function playStyles(p, pg) { return [pg]; }
    function captureOU() { return null; }
    function captureMatchup() { return null; }

    return { tiles: tiles, playStyles: playStyles, collectOU: collectOU,
             matchupLegs: matchupLegs, captureOU: captureOU, captureMatchup: captureMatchup };
  }
  return { create: create };
})();
