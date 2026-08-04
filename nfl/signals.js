/* ============================================================================
 * JTT NFL — signals.js  (window.JTTSignals for the unified shell)
 * ----------------------------------------------------------------------------
 * Mirrors AFL/signals.js so the shell can load either interchangeably.
 * The shell calls JTTSignals.create(deps) and, at render time, uses:
 *     collectOU(teamList, kind)   -> Green Lights (over) / Death Riders (under)
 *     matchupLegs(players, opp)   -> Matchup Multi legs
 * Unlike AFL (disposals-only), NFL's OU signals are MULTI-MARKET — each leg
 * carries its own market (.market/.mktLabel). NFL's scoreOverLine/scoreUnderLine
 * take the market as a 4th arg (provided by nfl/scoring.js).
 *
 * Deps injected by the shell's _signals():
 *   JTTScoring, oddsFor(name,mkt), nextOpp(team), playersOnTeam(team),
 *   hasAnyOdds(), SIGNAL_MIN_ODDS{over,under}, priceForLine(name,mkt,line),
 *   snapToRung, logsFor(name), abbr, curSeason, players, dvp
 * ========================================================================== */
(function (root, factory) {
  var mod = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = mod;   // Node (capture job)
  root.JTTSignals = mod;                                                        // browser (shell)
})(typeof self !== 'undefined' ? self : (typeof globalThis !== 'undefined' ? globalThis : this), function () {
  'use strict';

  // OU markets NFL fires Green Lights / Death Riders across (label shown on the card).
  var OU_MARKETS = [['passYds','Pass Yds'],['rushYds','Rush Yds'],['recYds','Rec Yds'],
                    ['receptions','Receptions'],['rushAtt','Rush Att'],['passAtt','Pass Att'],
                    ['rushRecYds','Rush+Rec Yds'],['tackles','Tackles+Ast']];
  // Matchup Multi markets: [key, season-avg floor, line-back-off]. A leg only qualifies
  // when the opponent is soft for that market (DVP) and a real posted line exists.
  var MM_MARKETS = [['passYds','Pass Yds',180,20],['rushYds','Rush Yds',40,8],['recYds','Rec Yds',40,8],
                    ['receptions','Receptions',3.5,1],['rushAtt','Rush Att',10,2],
                    ['passTds','Pass TDs',1.4,0],['tackles','Tackles+Ast',6.5,1]];
  var MM_DVP_MIN = 6;   // opponent must concede >= +6% for this market/position vs league

  function create(deps) {
    var JTTScoring      = deps.JTTScoring;
    var oddsFor         = deps.oddsFor;
    var nextOpp         = deps.nextOpp;
    var playersOnTeam   = deps.playersOnTeam;
    var hasAnyOdds      = deps.hasAnyOdds;
    var SIGNAL_MIN_ODDS = deps.SIGNAL_MIN_ODDS || { over: null, under: null };
    var priceForLine    = deps.priceForLine || function () { return null; };
    var _pos = function (p) { return (JTTScoring && JTTScoring.POS_TO_DVP && JTTScoring.POS_TO_DVP[p.position]) || p.position; };

    // ---- Green Lights (kind='over') / Death Riders (kind='under') — multi-market ----
    function collectOU(teamList, kind) {
      if (!JTTScoring || !hasAnyOdds()) return [];
      var side = kind === 'over' ? 'over' : 'under';
      var minOdds = SIGNAL_MIN_ODDS[side];
      var out = [];
      teamList.forEach(function (team) {
        var opp = nextOpp(team); if (!opp) return;
        playersOnTeam(team).forEach(function (p) {
          OU_MARKETS.forEach(function (mkt) {
            var mk = mkt[0], ml = mkt[1];
            var od = oddsFor(p.name, mk);                       // posted line must exist…
            if (!od || od.line == null) return;
            var price = od[side];
            if (price == null) return;                          // …with a price this side
            if (minOdds != null && price < minOdds) return;     // …above the signal's floor
            var r = kind === 'over'
              ? JTTScoring.scoreOverLine(p, opp, od.line, mk)
              : JTTScoring.scoreUnderLine(p, opp, od.line, mk);
            if (r) { r.team = team; r.oppName = opp; r.odds = price; r.book = od.book; r.mktLabel = ml; r.market = mk; out.push(r); }
          });
        });
      });
      return out.sort(function (a, b) { return kind === 'over' ? b.score - a.score : a.score - b.score; });
    }

    // ---- Matchup Multi legs: soft-matchup overs with a posted line ----
    function matchupLegs(players, opp) {
      if (!JTTScoring || !opp) return [];
      var out = [];
      players.forEach(function (p) {
        var pos = _pos(p);
        MM_MARKETS.forEach(function (m) {
          var k = m[0], lbl = m[1], floor = m[2], back = m[3];
          var avg = p[k] || 0; if (avg < floor) return;
          var dvp = JTTScoring.getDVPPct ? JTTScoring.getDVPPct(opp, pos, k) : null;
          if (dvp == null || dvp < MM_DVP_MIN) return;          // opponent must be soft here
          var line = (k === 'passTds') ? 1 : Math.round(avg) - back;
          if (line < 1 || avg <= line) return;
          var od = priceForLine(p.name, k, line);
          if (!od || od.price == null) return;                 // only real posted lines
          out.push({ p: p, opp: opp, statKey: k, line: line,
                     betLabel: line + '+ ' + lbl,
                     _odds: { price: od.price, book: od.book }, _dvp: dvp });
        });
      });
      return out.sort(function (a, b) { return (b._dvp - a._dvp) || (b._odds.price - a._odds.price); });
    }

    // ---- render helpers injected by the shell (so tiles produce shell-consistent HTML) ----
    var getData   = deps.getData   || function () { return {}; };
    var getScope  = deps.getScope  || function () { return null; };
    var esc       = deps.esc       || function (s) { return String(s == null ? '' : s); };
    var fmt       = deps.fmt       || function (v, d) { return v == null ? '\u2014' : (+v).toFixed(d == null ? 1 : d); };
    var abbr      = deps.abbr      || function (t) { return String(t || '').slice(0, 3).toUpperCase(); };
    var degWrap   = deps.degWrap   || function (i, t, items) { return (items && items.length) ? items.join('') : ''; };
    var emptyState = deps.emptyState || function () { return ''; };
    var posShort  = deps.posShort  || function (p) { return String(p || '').slice(0, 3).toUpperCase(); };
    var _byGame   = deps._byGame   || function (items, tf, rf) { return (items || []).map(rf); };
    var _fc       = deps._fc       || function (arr) { return arr || []; };
    var _degBadges = deps._degBadges || function () { return ''; };
    var dvpRank   = deps.dvpRank   || function () { return null; };
    var bookName  = deps.bookName  || function (b) { return b || ''; };
    var byName    = deps.byName    || function () { return null; };
    var logsFor   = deps.logsFor   || function () { return []; };
    var curSeason = deps.curSeason || function () { return ''; };
    var players   = deps.players   || [];
    var degRow    = deps.degRow    || function (n) { return '<div>' + n + '</div>'; };
    var multiLineTag = deps.multiLineTag || function () { return ''; };
    var lineTag   = deps.lineTag   || function () { return ''; };
    var teamMap   = deps.teamMap   || function () { return {}; };
    var _avg = function (a) { return a.length ? a.reduce(function (x, y) { return x + y; }, 0) / a.length : 0; };
    function sameDivision(a, b) { var tm = teamMap(), ta = tm[a], tb = tm[b]; return !!(ta && tb && ta.division && ta.division === tb.division); }

    /* ===================== RESEARCH TILES ===================== */
    // Each tile is a data producer + bespoke card that returns HTML for the shell's Degen Crew.
    // Ported from nfl/index.html; the shell delegates renderTile(k) -> tiles[k]() for NFL.

    // Weather Watch — condition-driven angles (wind kills the deep ball, rain funnels runs).
    var WX_WIND = 25;   // km/h sustained
    function weatherWatch() {
      var scope = getScope(), D = getData(), out = [];
      (D.fixture || []).forEach(function (g) {
        if (scope && !scope.has(g.home) && !scope.has(g.away)) return;
        var w = (D.weather || []).find(function (x) { return x.home === g.home && x.away === g.away; });
        if (!w) return;
        if (w.roof && w.roof !== 'outdoors' && w.roof !== 'open') return;      // dome — no angle
        var windy = w.wind != null && +w.wind >= WX_WIND;
        var wet = w.code != null && +w.code >= 61;                             // WMO: rain and worse
        if (!windy && !wet) return;
        var headline = (windy ? Math.round(w.wind) + ' km/h wind' : '') + (windy && wet ? ' + ' : '') + (wet ? (w.desc || 'rain') : '');
        var runners = [g.home, g.away].map(function (t) {
          return playersOnTeam(t).filter(function (p) { return p.position === 'RB' && (p.matches || 0) >= 4; })
            .sort(function (a, b) { return (b.rushAtt || 0) - (a.rushAtt || 0); })[0];
        }).filter(Boolean);
        var deep = [g.home, g.away].reduce(function (a, t) {
          return a.concat(playersOnTeam(t).filter(function (p) { return (p.aDot || 0) >= 13 && (p.tgtShare || 0) >= 15 && (p.matches || 0) >= 4; }));
        }, []).slice(0, 3);
        out.push({ g: g, w: w, headline: headline, windy: windy, wet: wet, runners: runners, deep: deep });
      });
      return out;
    }
    function wxCard(x) {
      var chips = [];
      x.runners.forEach(function (r) {
        var q = esc(r.name).replace(/'/g, "\\'");
        chips.push('<span class="lu-p" style="color:#22c55e;border-color:#22c55e55;cursor:pointer" onclick="openPlayer(\'' + q + '\')">rush funnel \u00b7 ' + esc(r.name) + ' ' + fmt(r.rushAtt, 1) + ' att/g</span>');
      });
      x.deep.forEach(function (p) {
        var q = esc(p.name).replace(/'/g, "\\'");
        chips.push('<span class="lu-p" style="color:#f97316;border-color:#f9731655;cursor:pointer" onclick="openPlayer(\'' + q + '\')">fade deep \u00b7 ' + esc(p.name) + ' aDot ' + (p.aDot || 0).toFixed(1) + '</span>');
      });
      if (x.windy) chips.push('<span class="lu-p">unders lean \u00b7 deep passing degrades in ' + Math.round(x.w.wind) + ' km/h</span>');
      return '<div class="lc-card"><div class="lc-hd"><span class="lc-nm"><i class="ti ti-' + (x.wet ? 'cloud-rain' : 'wind') + '"></i> ' +
        abbr(x.g.home) + ' v ' + abbr(x.g.away) + '</span><span class="lc-meta">' + esc(x.headline) + (x.g.venue ? ' \u00b7 ' + esc(x.g.venue) : '') + '</span></div>' +
        (chips.length ? '<div class="lu-grid" style="gap:5px">' + chips.join(' ') + '</div>' : '') + '</div>';
    }

    // Tuddy Targets — players top-5 in a red-zone usage stat for their position, facing a
    // bottom-10 TD defence for that position. Cards show L10 / H2H / season TD-game rates.
    var _TUDDY_STATS = {
      WR: [['rzTgt','RZ targets'],['rzTgtPct','RZ target %'],['rzRec','RZ receptions'],['rzRecTd','RZ TDs'],
           ['i10Tgt','inside-10 targets'],['i10TgtPct','inside-10 target %'],['i10Rec','inside-10 receptions'],['i10RecTd','inside-10 TDs']],
      RB: [['rzTgt','RZ targets'],['rzTgtPct','RZ target %'],['rzRec','RZ receptions'],['rzRecTd','RZ receiving TDs'],
           ['rzAtt','RZ carries'],['rzRushPct','RZ rush %'],['rzRushTd','RZ rush TDs'],
           ['i10Att','inside-10 carries'],['i10RushPct','inside-10 rush %'],['i10RushTd','inside-10 TDs'],
           ['i5Att','inside-5 carries'],['i5RushPct','inside-5 rush %'],['i5RushTd','inside-5 TDs']],
      QB: [['rzAtt','RZ carries'],['rzRushPct','RZ rush %'],['rzRushTd','RZ rush TDs']]
    };
    _TUDDY_STATS.TE = _TUDDY_STATS.WR;
    function _tdRate(logs) { var n = logs.length; return { h: logs.filter(function (r) { return (r.totalTds || 0) >= 1; }).length, n: n }; }
    function tdOddsTag(name) {
      var any = oddsFor(name, 'anytimeTd'), fst = oddsFor(name, 'firstTd');
      if (any && any.over != null && any.book) return ' \u00b7 ATD $' + any.over.toFixed(2) + ' (' + bookName(any.book) + ')';
      if (fst && fst.over != null && fst.book) return ' \u00b7 1st TD $' + fst.over.toFixed(2) + ' (' + bookName(fst.book) + ')';
      return '';
    }
    function snags() {
      var D = getData(), rz = D.redzone || []; if (!rz.length) return [];
      var byPos = {};
      rz.forEach(function (r) { var p = byName(r.player); if (p && _TUDDY_STATS[p.position]) (byPos[p.position] = byPos[p.position] || []).push(r); });
      var qual = {};
      Object.keys(byPos).forEach(function (pos) {
        var rows = byPos[pos];
        _TUDDY_STATS[pos].forEach(function (kl) {
          var k = kl[0], l = kl[1];
          rows.filter(function (r) { return (r[k] || 0) > 0; }).sort(function (a, b) { return (b[k] || 0) - (a[k] || 0); }).slice(0, 5).forEach(function (r, i) {
            var p = byName(r.player); if (!p) return;
            var q = qual[r.player] = qual[r.player] || { p: p, chips: [] };
            q.chips.push({ rank: i + 1, l: '#' + (i + 1) + ' ' + pos + ' ' + l });
          });
        });
      });
      var out = [], cs = curSeason();
      Object.keys(qual).forEach(function (nm) {
        var e = qual[nm], p = e.p, chips = e.chips;
        var opp = nextOpp(p.team); if (!opp) return;
        var dr = dvpRank(opp, p.position, 'anytimeTd'); if (!dr) return;
        if (dr.rank > 10) return;                              // bottom-10 (softest) TD defence for the position
        var sorted = (logsFor(p.name) || []).slice();
        var l10 = _tdRate(sorted.slice(-10));
        var h2hLogs = sorted.filter(function (r) { return r.opponent === opp; });
        var h2h = h2hLogs.length ? _tdRate(h2hLogs) : null;
        var ssn = _tdRate(sorted.filter(function (r) { return String(r.Year) === String(cs); }));
        chips.sort(function (a, b) { return a.rank - b.rank; });
        out.push({ p: p, opp: opp, dvpRank: dr.rank, dvpPct: dr.pct, chips: chips.slice(0, 4), l10: l10, h2h: h2h, ssn: ssn, season: cs });
      });
      return out.sort(function (a, b) { return (a.dvpRank - b.dvpRank) || (b.chips.length - a.chips.length); });
    }
    function tuddyCard(c) {
      var q = esc(c.p.name).replace(/'/g, "\\'");
      var rate = function (r, lab) { return r ? ('<span><b>' + lab + '</b> ' + r.h + '/' + r.n + '</span>') : ''; };
      var rates = [rate(c.l10, 'L10'), rate(c.h2h, 'H2H'), rate(c.ssn, c.season)].filter(Boolean).join(' \u00b7 ');
      var chips = ['<span class="lu-p" style="color:#f59e0b;border-color:#f59e0b55">#' + c.dvpRank + ' TDs allowed ' + posShort(c.p.position) + '</span>']
        .concat(c.chips.map(function (ch) { return '<span class="lu-p">' + esc(ch.l) + '</span>'; })).join(' ');
      var od = tdOddsTag(c.p.name);
      return '<div class="lc-card" onclick="openPlayer(\'' + q + '\')">' +
        '<div class="lc-hd"><span class="lc-nm">' + esc(c.p.name) + '</span>' + _degBadges(c.p.name) +
        '<span class="lc-meta">' + posShort(c.p.position) + ' \u00b7 ' + abbr(c.p.team) + ' v ' + abbr(c.opp) + (od ? ' \u00b7' + od : '') + '</span></div>' +
        (rates ? '<div class="tp-body-meta" style="border:0;padding:2px 0 6px">TD games: ' + rates + '</div>' : '') +
        '<div class="lu-grid" style="gap:5px">' + chips + '</div></div>';
    }

    // Elite Matchups — top-10 in the league for a stat, facing a bottom-5 defence for it.
    var ELITE_DEFS = [{ k: 'passYds', l: 'Pass Yds' }, { k: 'rushYds', l: 'Rush Yds' }, { k: 'recYds', l: 'Rec Yds' },
                      { k: 'receptions', l: 'Receptions' }, { k: 'rushAtt', l: 'Rush Att' }, { k: 'targets', l: 'Targets' },
                      { k: 'tackles', l: 'Tackles+Ast' }];
    function elite() {
      var pool = (players || []).filter(function (p) { return (p.matches || 0) >= 4; });
      var out = [];
      ELITE_DEFS.forEach(function (def) {
        pool.slice().sort(function (a, b) { return (b[def.k] || 0) - (a[def.k] || 0); }).slice(0, 10).forEach(function (p, i) {
          var opp = nextOpp(p.team); if (!opp) return;
          var dr = dvpRank(opp, p.position, def.k); if (!dr) return;
          if (dr.rank > 5) return;                             // bottom-5 (softest) defence for this stat + position
          out.push({ p: p, def: def, opp: opp, rank: i + 1, val: p[def.k] || 0, dvpRank: dr.rank, dvpPct: dr.pct, dvpTotal: dr.total });
        });
      });
      return out.sort(function (a, b) { return a.dvpRank - b.dvpRank; });
    }

    // Bunnies — divisional rivals only (they meet twice a year, so H2H means something).
    // Two flavours: AVG (averages more vs this opp than vs any other) and LINE (cleared the
    // posted line in every H2H meeting).
    var BUNNY_STATS = [{ k: 'recYds', l: 'Rec Yds', min: 30 }, { k: 'receptions', l: 'Receptions', min: 3 },
                       { k: 'rushYds', l: 'Rush Yds', min: 30 }, { k: 'passYds', l: 'Pass Yds', min: 150 }];
    function bunnies() {
      var out = [];
      (players || []).filter(function (p) { return (p.matches || 0) >= 3; }).forEach(function (p) {
        var opp = nextOpp(p.team); if (!opp) return;
        if (!sameDivision(p.team, opp)) return;                 // divisional rivals only
        var byOpp = {};
        (logsFor(p.name) || []).forEach(function (r) { if (r.opponent) (byOpp[r.opponent] = byOpp[r.opponent] || []).push(r); });
        var vt = byOpp[opp]; if (!vt || vt.length < 3) return;  // need >=3 H2H meetings vs current opp
        BUNNY_STATS.forEach(function (s) {
          if ((p[s.k] || 0) < s.min) return;
          var thisAvg = _avg(vt.map(function (r) { return r[s.k] || 0; }));
          var best = null, bestOpp = null;
          Object.keys(byOpp).forEach(function (o) {
            var rs = byOpp[o]; if (o === opp || rs.length < 2) return;
            var a = _avg(rs.map(function (r) { return r[s.k] || 0; }));
            if (best == null || a > best) { best = a; bestOpp = o; }
          });
          var avgBunny = (best != null) && (thisAvg > best);
          var diffPct = (best != null && best > 0) ? ((thisAvg - best) / best) * 100 : null;
          var posted = oddsFor(p.name, s.k);
          var line = (posted && posted.line != null) ? posted.line : null;
          var lineBunny = line != null && vt.every(function (r) { return (r[s.k] || 0) >= line; });
          if (!avgBunny && !lineBunny) return;
          out.push({ p: p, opp: opp, stat: s, thisAvg: thisAvg, best: best, bestOpp: bestOpp, diffPct: diffPct, games: vt.length, avgBunny: avgBunny, lineBunny: lineBunny, line: line });
        });
      });
      return out.sort(function (a, b) { return (b.lineBunny - a.lineBunny) || ((b.diffPct || 0) - (a.diffPct || 0)); });
    }

    // tiles: key -> () => HTML. The shell's renderTile(k) calls tiles[k]() for NFL.
    // Keys mirror the shell's tile vocabulary. (More ported in batches.)
    var M = function (v) { return fmt(v, 1); };
    var tiles = {
      bunnies: function () {
        var rows = _byGame(_fc(bunnies(), 6), function (b) { return b.p.team; }, function (b) {
          var sub = b.stat.l + ' \u00b7 vs ' + abbr(b.opp) + ' ' + M(b.thisAvg) + (b.best != null ? ' (next best ' + M(b.best) + ')' : '') + ' \u00b7 ' + b.games + ' H2H' + lineTag(b.p.name, b.stat.k);
          return degRow(b.p.name, '#3b82f6', { v1: M(b.thisAvg), l1: b.stat.l, v2: (b.lineBunny ? b.games + '/' + b.games : (b.diffPct != null ? '+' + b.diffPct.toFixed(0) + '%' : '\u2014')), l2: (b.lineBunny ? 'cleared' : 'v field') }, sub);
        });
        return degWrap('ti-carrot', 'Bunnies', rows, 'c-blue');
      },
      elite: function () {
        var M = function (v) { return fmt(v, 1); };
        var rows = _byGame(_fc(elite(), 8, 40), function (s) { return s.p.team; }, function (s) {
          return degRow(s.p.name, '#22c55e', { v1: M(s.val), l1: s.def.l, v2: '#' + s.dvpRank, l2: 'softest' },
            posShort(s.p.position) + ' \u00b7 ' + abbr(s.p.team) + ' v ' + abbr(s.opp) + ' \u00b7 ' + M(s.val) + ' ' + s.def.l.toLowerCase() + multiLineTag(s.p.name, s.def.k));
        });
        return degWrap('ti-trophy', 'Elite Matchups', rows, 'c-green');
      },
      paydirt: function () {
        var rows = _byGame(_fc(snags(), 6), function (s) { return s.p.team; }, tuddyCard);
        return degWrap('ti-ball-american-football', 'Tuddy Targets', rows, 'c-amber');
      },
      wx: function () {
        var arr = weatherWatch();
        if (!arr.length) return emptyState('ti-wind', 'Weather Watch is calm', 'No outdoor game on the slate has wind \u2265' + WX_WIND + ' km/h or rain in the forecast.');
        return degWrap('ti-wind', 'Weather Watch', arr.map(wxCard), 'c-cyan');
      }
    };

    // Capture helpers exist for API parity with AFL/signals.js. The live shell never calls
    // them (only the offline ledger job does); NFL's ledger pipeline can flesh these out later.
    function captureOU() { return []; }
    function captureMatchup() { return []; }

    return { collectOU: collectOU, matchupLegs: matchupLegs, tiles: tiles,
             captureOU: captureOU, captureMatchup: captureMatchup };
  }

  return { create: create };
});
