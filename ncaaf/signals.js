/* ============================================================================
 * JTT NCAAF — signals.js  (reuses NFL gridiron engines; college engines = Phase 2)
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
    var teamLogo  = deps.teamLogo  || function () { return ''; };
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
    var underTag  = deps.underTag  || function () { return ''; };
    var _fixtureSet = deps._fixtureSet || function () { return new Set(); };
    var _roundNum = deps._roundNum || function () { return 0; };
    var curLogs   = deps.curLogs   || function () { return []; };
    var windowDvp = deps.windowDvp || function () { return null; };
    var formNote  = deps.formNote  || function () { return ''; };
    var muTag     = deps.muTag     || function () { return ''; };
    var streakOdds = deps.streakOdds || function () { return ''; };
    var logsByName = deps.logsByName || function () { return []; };
    var isFocused = deps.isFocused || function () { return false; };
    var estimateHitProb = deps.estimateHitProb || function () { return 0.5; };
    var isPlaying = deps.isPlaying || function () { return true; };
    var _pairRho  = deps._pairRho  || function () { return 0; };
    var _clusterJoint = deps._clusterJoint || function (legs) { return legs.reduce(function (a, l) { return a * (l.prob || 0); }, 1); };
    var POS_TO_DVP = (JTTScoring && JTTScoring.POS_TO_DVP) || {};
    var _dvpPos = function (p) { return POS_TO_DVP[p.position] || p.position; };
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

    // Bogey — Bunnies' mirror: divisional rival the player struggles against (worst matchup /
    // never cleared the posted line in any H2H meeting).
    var BOGEY_STATS = [{ k: 'recYds', l: 'Rec Yds', min: 30 }, { k: 'rushYds', l: 'Rush Yds', min: 30 }, { k: 'passYds', l: 'Pass Yds', min: 150 }];
    function bogey() {
      var out = [];
      (players || []).filter(function (p) { return (p.matches || 0) >= 3; }).forEach(function (p) {
        var opp = nextOpp(p.team); if (!opp) return;
        if (!sameDivision(p.team, opp)) return;
        var byOpp = {};
        (logsFor(p.name) || []).forEach(function (r) { if (r.opponent) (byOpp[r.opponent] = byOpp[r.opponent] || []).push(r); });
        var vt = byOpp[opp]; if (!vt || vt.length < 2) return;
        BOGEY_STATS.forEach(function (s) {
          if ((p[s.k] || 0) < s.min) return;
          var thisAvg = _avg(vt.map(function (r) { return r[s.k] || 0; }));
          var worst = null, worstOpp = null;
          Object.keys(byOpp).forEach(function (o) {
            var rs = byOpp[o]; if (o === opp || rs.length < 2) return;
            var a = _avg(rs.map(function (r) { return r[s.k] || 0; }));
            if (worst == null || a < worst) { worst = a; worstOpp = o; }
          });
          var avgBogey = (worst == null) || (thisAvg < worst);
          var diffPct = (worst != null && worst > 0) ? ((worst - thisAvg) / worst) * 100 : null;
          var posted = oddsFor(p.name, s.k);
          var line = (posted && posted.line != null) ? posted.line : null;
          var lineBogey = line != null && vt.every(function (r) { return (r[s.k] || 0) < line; });
          if (!avgBogey && !lineBogey) return;
          out.push({ p: p, opp: opp, stat: s, thisAvg: thisAvg, worst: worst, worstOpp: worstOpp, diffPct: diffPct, games: vt.length, avgBogey: avgBogey, lineBogey: lineBogey, line: line });
        });
      });
      return out.sort(function (a, b) { return (b.lineBogey - a.lineBogey) || ((b.diffPct || 0) - (a.diffPct || 0)); });
    }

    // ---- Streakers — N straight games clearing a market-typical line ----
    function realisticLine2(k, avg) {
      if (k === 'passYds') { if (avg < 180) return null; var a = Math.round(avg) - 25; return a >= 150 ? a : null; }
      if (k === 'passAtt') { if (avg < 28) return null; var b = Math.round(avg) - 4; return b >= 24 ? b : null; }
      if (k === 'rushYds' || k === 'recYds') { if (avg < 40) return null; var c = Math.round(avg) - 10; return c >= 30 ? c : null; }
      if (k === 'rushRecYds') { if (avg < 50) return null; var d = Math.round(avg) - 12; return d >= 40 ? d : null; }
      if (k === 'receptions') { if (avg < 3.5) return null; var e = Math.floor(avg) - 1; return e >= 2 ? e : null; }
      if (k === 'rushAtt') { if (avg < 10) return null; var f = Math.round(avg) - 3; return f >= 8 ? f : null; }
      if (k === 'anytimeTd') { return avg >= 0.55 ? 1 : null; }
      if (k === 'tackles') { if (avg < 6.5) return null; var g = Math.floor(avg) - 1; return g >= 5 ? g : null; }
      return null;
    }
    var STREAK_STATS = [['passYds', 'Pass Yds'], ['rushYds', 'Rush Yds'], ['recYds', 'Rec Yds'], ['receptions', 'Receptions'],
                        ['rushAtt', 'Rush Att'], ['rushRecYds', 'Rush+Rec Yds'], ['anytimeTd', 'Anytime TD'], ['tackles', 'Tackles+Ast']];
    function streakers() {
      var teams = _fixtureSet(), out = [];
      (players || []).filter(function (p) { return teams.has(p.team); }).forEach(function (p) {
        var sorted = (logsFor(p.name) || []).slice().sort(function (a, b) { var y = (+a.Year || 0) - (+b.Year || 0); return y || _roundNum(a.RoundName) - _roundNum(b.RoundName); });
        STREAK_STATS.forEach(function (kl) {
          var k = kl[0], l = kl[1];
          var line = realisticLine2(k, p[k] || 0); if (!line) return;
          var vals = sorted.map(function (r) { return r[k] || 0; }); if (vals.length < 5) return;
          var streak = 0; for (var i = vals.length - 1; i >= 0; i--) { if (vals[i] >= line) streak++; else break; }
          if (streak < 7) return;                              // 7 straight (AFL's 10-gate scaled to 17-game seasons)
          var opp = nextOpp(p.team), pos = _dvpPos(p);
          out.push({ p: p, stat: l, statKey: k, line: line, streak: streak, opp: opp,
            dvpPct: opp ? JTTScoring.getDVPPct(opp, pos, k) : null, runDvp: windowDvp(p, k, sorted.slice(-streak)),
            rate: vals.filter(function (v) { return v >= line; }).length / vals.length });
        });
      });
      return out.sort(function (a, b) { return b.streak - a.streak; });
    }

    // ---- Form Alerts — L3 vs season swing (spike / drop) ----
    function formAlerts(dir) {
      var teams = _fixtureSet(), out = [];
      var STATS = [['passYds', 'Pass Yds', 150], ['rushYds', 'Rush Yds', 30], ['recYds', 'Rec Yds', 30], ['receptions', 'Receptions', 3], ['rushAtt', 'Rush Att', 8], ['fanPts', 'Fan Pts', 8], ['tackles', 'Tackles+Ast', 5]];
      var ALERT_THRESHOLD = 0.25, ALERT_MIN_L3 = 3;
      (players || []).filter(function (p) { return teams.has(p.team); }).forEach(function (p) {
        var cur = (curLogs(p.name) || []).slice().sort(function (a, b) { return _roundNum(b.RoundName) - _roundNum(a.RoundName); });
        if (cur.length < ALERT_MIN_L3) return;
        var base = cur.length >= 10 ? cur : (logsFor(p.name) || []).slice(-10);
        STATS.forEach(function (klm) {
          var k = klm[0], l = klm[1], min = klm[2];
          var seasonAvg = _avg(base.map(function (r) { return r[k] || 0; })); if (seasonAvg <= 0) return;
          var l3 = _avg(cur.slice(0, 3).map(function (r) { return r[k] || 0; }));
          var swing = (l3 - seasonAvg) / seasonAvg;
          if (Math.abs(swing) < ALERT_THRESHOLD) return;
          var spiking = swing > 0;
          if (dir === 'spike' && !spiking) return; if (dir === 'drop' && spiking) return;
          if (spiking && l3 < min) return;
          if (!spiking && seasonAvg < min) return;
          var opp = nextOpp(p.team), pos = _dvpPos(p);
          out.push({ p: p, stat: l, statKey: k, seasonAvg: seasonAvg, l3: l3, swing: swing, spiking: spiking, opp: opp,
            thisDvp: opp ? JTTScoring.getDVPPct(opp, pos, k) : null, runDvp: windowDvp(p, k, cur.slice(0, 3)) });
        });
      });
      return out.sort(function (a, b) { return Math.abs(b.swing) - Math.abs(a.swing); });
    }

    // ---- Usage Trend — role INPUTS (snaps, target share, carries) L3 vs season ----
    var USAGE_MIN_G = 6;
    function usageTrend() {
      var teams = _fixtureSet(), cs = curSeason(), out = [];
      (players || []).filter(function (p) { return teams.has(p.team) && (p.matches || 0) >= USAGE_MIN_G; }).forEach(function (p) {
        var logs = (logsByName(p.name) || []).filter(function (r) { return String(r.Year) === String(cs); });
        if (logs.length < USAGE_MIN_G) return;
        var l3 = logs.slice(-3), base = logs.slice(0, -3); if (base.length < 3) return;
        var av = function (arr, k) { return arr.reduce(function (s, r) { return s + (r[k] || 0); }, 0) / arr.length; };
        var deltas = [];
        var dSnap = av(l3, 'snapPct') - av(base, 'snapPct');
        if (Math.abs(dSnap) >= 8 && av(l3, 'snapPct') > 0) deltas.push({ k: 'snapPct', l: 'snaps', d: dSnap, u: 'pts' });
        if (['WR', 'TE', 'RB'].indexOf(p.position) >= 0) {
          var dTs = av(l3, 'tgtShare') - av(base, 'tgtShare'); if (Math.abs(dTs) >= 4) deltas.push({ k: 'tgtShare', l: 'target share', d: dTs, u: 'pts' });
          var dTg = av(l3, 'targets') - av(base, 'targets'); if (Math.abs(dTg) >= 2) deltas.push({ k: 'targets', l: 'targets/g', d: dTg, u: '' });
        }
        if (p.position === 'RB') { var dRa = av(l3, 'rushAtt') - av(base, 'rushAtt'); if (Math.abs(dRa) >= 3) deltas.push({ k: 'rushAtt', l: 'carries/g', d: dRa, u: '' }); }
        if (!deltas.length) return;
        var score = deltas.reduce(function (s, x) { return s + Math.abs(x.d); }, 0);
        var dir = deltas.reduce(function (s, x) { return s + x.d; }, 0) >= 0 ? 'asc' : 'fade';
        out.push({ p: p, opp: nextOpp(p.team), deltas: deltas, dir: dir, score: score, n: logs.length });
      });
      return out.sort(function (a, b) { return b.score - a.score; });
    }
    function usageCard(c) {
      var q = esc(c.p.name).replace(/'/g, "\\'"), col = c.dir === 'asc' ? '#22c55e' : '#f97316';
      var chips = c.deltas.map(function (x) { return '<span class="lu-p" style="color:' + (x.d >= 0 ? '#22c55e' : '#f97316') + '">' + (x.d >= 0 ? '+' : '') + x.d.toFixed(1) + (x.u ? ' ' + x.u : '') + ' ' + x.l + ' (L3 v season)</span>'; }).join(' ');
      return '<div class="lc-card" onclick="openPlayer(\'' + q + '\')"><div class="lc-hd"><span class="lc-nm">' + esc(c.p.name) + '</span>' + _degBadges(c.p.name) +
        '<span class="lc-meta" style="color:' + col + '">' + (c.dir === 'asc' ? 'ASCENDING' : 'FADING') + ' \u00b7 ' + posShort(c.p.position) + ' \u00b7 ' + abbr(c.p.team) + (c.opp ? ' v ' + abbr(c.opp) : '') + '</span></div>' +
        '<div class="lu-grid" style="gap:5px">' + chips + '</div></div>';
    }

    // ---- Chunk Plays — longest reception/rush/completion clearance streaks ----
    var CHUNK_DEFS = [
      { k: 'longRec', l: 'Longest Rec', thr: 20, pos: ['WR', 'TE', 'RB'], exp: 'expRec', expL: 'explosive catches', style: 'aDot' },
      { k: 'longRush', l: 'Longest Rush', thr: 12, pos: ['RB', 'QB'], exp: 'expRush', expL: 'explosive runs', style: 'ypc' },
      { k: 'longComp', l: 'Longest Comp', thr: 35, pos: ['QB'], exp: 'expRec', expL: 'explosive passes', style: null }
    ];
    var CHUNK_HIT = 0.70, CHUNK_MIN_G = 6, CHUNK_CLAMP = -12;
    function _chunkSimilar(p, def, opp, line) {
      if (!opp) return null;
      var metric = def.k === 'longRush' ? 'ypc' : (def.k === 'longComp' ? 'passYds' : 'aDot');
      var mine = p[metric] || 0; if (!mine) return null;
      var pool = (players || []).filter(function (q) { return q.name !== p.name && q.position === p.position && (q.matches || 0) >= 3 && (q[metric] || 0) > 0; })
        .map(function (q) { return { q: q, d: Math.abs((q[metric] || 0) - mine) }; }).sort(function (a, b) { return a.d - b.d; }).slice(0, 8);
      var h = 0, n = 0, used = 0;
      pool.forEach(function (pair) {
        var vs = (logsByName(pair.q.name) || []).filter(function (r) { return r.opponent === opp && r[def.k] != null; });
        if (!vs.length) return; used++;
        vs.forEach(function (r) { n++; if ((r[def.k] || 0) >= line) h++; });
      });
      return n >= 3 ? { h: h, n: n, used: used } : null;
    }
    function _chunkStyle(p, def) {
      if (def.style === 'aDot') { var a = p.aDot || 0; if (!a) return null;
        if (a >= 12) return { txt: 'deep threat \u00b7 aDot ' + a.toFixed(1), pts: 8, c: '#22c55e' };
        if (a <= 8) return { txt: 'short-area \u00b7 aDot ' + a.toFixed(1), pts: -8, c: '#f97316' };
        return { txt: 'aDot ' + a.toFixed(1), pts: 0, c: 'var(--text-3)' }; }
      if (def.style === 'ypc') { var y = p.ypc || 0; if (!y) return null;
        if (y >= 4.8) return { txt: 'explosive runner \u00b7 ' + y.toFixed(1) + ' ypc', pts: 6, c: '#22c55e' };
        if (y <= 3.6) return { txt: 'grinder \u00b7 ' + y.toFixed(1) + ' ypc', pts: -6, c: '#f97316' };
        return { txt: y.toFixed(1) + ' ypc', pts: 0, c: 'var(--text-3)' }; }
      return null;
    }
    function chunkPlays() {
      var teams = _fixtureSet(), out = [];
      (players || []).filter(function (p) { return teams.has(p.team) && (p.matches || 0) >= 3; }).forEach(function (p) {
        var opp = nextOpp(p.team);
        CHUNK_DEFS.forEach(function (def) {
          if (def.pos.indexOf(p.position) < 0) return;
          var vals = (logsFor(p.name) || []).map(function (r) { return r[def.k]; }).filter(function (v) { return v != null; });
          if (vals.length < CHUNK_MIN_G) return;
          var posted = oddsFor(p.name, def.k);
          var line = (posted && posted.line != null) ? Math.ceil(posted.line) : def.thr;
          var hits = vals.filter(function (v) { return v >= line; }).length, rate = hits / vals.length;
          if (rate < CHUNK_HIT) return;
          var pos = _dvpPos(p);
          var expDvp = opp ? JTTScoring.getDVPPct(opp, pos, def.exp) : null;
          if (expDvp != null && expDvp <= CHUNK_CLAMP) return;  // opp suppresses explosives — history means little
          var longDvp = opp ? JTTScoring.getDVPPct(opp, pos, def.k) : null;
          var style = _chunkStyle(p, def), sim = _chunkSimilar(p, def, opp, line), avg = _avg(vals);
          var l5 = vals.slice(-5).map(function (v) { return Math.round(v); }).join(', ');
          var score = rate * 100 + (expDvp || 0) * 0.6 + (longDvp || 0) * 0.3 + (style ? style.pts : 0) + (sim ? ((sim.h / sim.n) - 0.5) * 30 : 0);
          out.push({ p: p, opp: opp, def: def, line: line, hits: hits, n: vals.length, rate: rate, avg: avg, expDvp: expDvp, longDvp: longDvp, style: style, sim: sim, l5: l5, score: score, posted: !!(posted && posted.line != null) });
        });
      });
      return out.sort(function (a, b) { return b.score - a.score; });
    }
    function chunkCard(c) {
      var q = esc(c.p.name).replace(/'/g, "\\'"), od = lineTag(c.p.name, c.def.k), chips = [];
      if (c.expDvp != null) { var c1 = c.expDvp >= 10 ? '#22c55e' : c.expDvp >= 3 ? '#86efac' : c.expDvp <= -6 ? '#f97316' : 'var(--text-3)';
        chips.push('<span class="lu-p" style="color:' + c1 + ';border-color:' + c1 + '55">' + abbr(c.opp) + ' ' + (c.expDvp >= 0 ? 'allows +' : 'allows ') + c.expDvp.toFixed(0) + '% ' + c.def.expL + ' to ' + posShort(c.p.position) + 's</span>'); }
      if (c.longDvp != null) { var c2 = c.longDvp >= 8 ? '#22c55e' : c.longDvp <= -8 ? '#f97316' : 'var(--text-3)';
        chips.push('<span class="lu-p" style="color:' + c2 + ';border-color:' + c2 + '55">longest allowed ' + (c.longDvp >= 0 ? '+' : '') + c.longDvp.toFixed(0) + '% v league</span>'); }
      if (c.style) chips.push('<span class="lu-p" style="color:' + c.style.c + ';border-color:' + c.style.c + '55">' + esc(c.style.txt) + '</span>');
      if (c.sim) { var sr = c.sim.h / c.sim.n, c3 = sr >= 0.6 ? '#22c55e' : sr >= 0.4 ? '#eab308' : '#ef4444';
        chips.push('<span class="lu-p" style="color:' + c3 + ';border-color:' + c3 + '55">similar ' + posShort(c.p.position) + 's ' + c.sim.h + '/' + c.sim.n + ' cleared v ' + abbr(c.opp) + ' (' + c.sim.used + ' players)</span>'); }
      return '<div class="lc-card" onclick="openPlayer(\'' + q + '\')"><div class="lc-hd"><span class="lc-nm">' + esc(c.p.name) + '</span>' + _degBadges(c.p.name) +
        '<span class="lc-meta">' + posShort(c.p.position) + ' \u00b7 ' + abbr(c.p.team) + (c.opp ? ' v ' + abbr(c.opp) : '') + (od ? ' \u00b7' + od : '') + '</span></div>' +
        '<div class="tp-body-meta" style="border:0;padding:2px 0 6px"><b>' + c.def.l + ' ' + c.line + '+</b>' + (c.posted ? '' : ' (est line)') + ' \u00b7 hit ' + c.hits + '/' + c.n + ' (' + Math.round(c.rate * 100) + '%) \u00b7 avg ' + c.avg.toFixed(0) + ' \u00b7 L5 ' + c.l5 + '</div>' +
        (chips.length ? '<div class="lu-grid" style="gap:5px">' + chips.join(' ') + '</div>' : '') + '</div>';
    }

    // ---- Next Man Up — who absorbs volume when a starter sits (splits without the absent player) ----
    var DEF_POS = (typeof Set !== 'undefined') ? new Set(['LB', 'DL', 'DB']) : { has: function (x) { return x === 'LB' || x === 'DL' || x === 'DB'; } };
    function _outList() {
      var outRe = /out|injured reserve|\bir\b|doubt/i;
      return ((getData().injury) || []).filter(function (r) { return outRe.test(String(r.Status || '')); });
    }
    function nextManUp() {
      var teams = _fixtureSet(), out = [];
      var GRP = function (p) { return p === 'QB' ? 'QB' : p === 'RB' ? 'RB' : (p === 'WR' || p === 'TE') ? 'REC' : null; };
      _outList().forEach(function (inj) {
        var star = byName(inj.Player); if (!star) return;
        if (!teams.has(star.team)) return;
        if ((star.snapPct || 0) < 45 && (star.fanPts || 0) < 8 && !DEF_POS.has(star.position)) return;   // fringe absences don't move volume
        var grp = GRP(star.position) || star.position;
        var withSet = new Set((logsByName(star.name) || []).map(function (r) { return r.MatchId; }));
        var bens = [];
        playersOnTeam(star.team).forEach(function (b) {
          if (b.name === star.name || (b.matches || 0) < 3) return;
          if ((GRP(b.position) || b.position) !== grp) return;
          var logs = (logsByName(b.name) || []).filter(function (r) { return r.Team === star.team; });
          var wo = logs.filter(function (r) { return !withSet.has(r.MatchId); }), wi = logs.filter(function (r) { return withSet.has(r.MatchId); });
          if (wo.length < 2 || wi.length < 3) return;
          var av = function (a, k) { return a.reduce(function (s, r) { return s + (r[k] || 0); }, 0) / a.length; };
          var keys = grp === 'QB' ? [['passAtt', 'att/g'], ['passYds', 'pass yds']]
            : grp === 'RB' ? [['rushAtt', 'carries'], ['targets', 'targets']]
            : [['targets', 'targets'], ['tgtShare', 'tgt share'], ['recYds', 'rec yds']];
          var deltas = keys.map(function (kl) { var k = kl[0], l = kl[1]; return { k: k, l: l, wo: av(wo, k), wi: av(wi, k), d: av(wo, k) - av(wi, k) }; })
            .filter(function (x) { return Math.abs(x.d) >= (x.k === 'tgtShare' ? 2 : x.k === 'recYds' ? 8 : 1); });
          if (!deltas.length) return;
          bens.push({ b: b, deltas: deltas, nwo: wo.length, gain: deltas.reduce(function (s, x) { return s + Math.max(0, x.d); }, 0) });
        });
        if (!bens.length) return;
        bens.sort(function (a, b) { return b.gain - a.gain; });
        out.push({ star: star, status: inj.Status, injury: inj.Injury, opp: nextOpp(star.team), bens: bens.slice(0, 3), gain: bens[0].gain });
      });
      return out.sort(function (a, b) { return b.gain - a.gain; });
    }
    function nextCard(c) {
      var rows = c.bens.map(function (x) {
        var q = esc(x.b.name).replace(/'/g, "\\'");
        var chips = x.deltas.map(function (d) { return '<span class="lu-p" style="color:' + (d.d >= 0 ? '#22c55e' : '#f97316') + '">' + (d.d >= 0 ? '+' : '') + d.d.toFixed(1) + ' ' + d.l + ' without (' + d.wo.toFixed(1) + ' v ' + d.wi.toFixed(1) + ')</span>'; }).join(' ');
        return '<div style="padding:6px 0;border-top:1px solid var(--line);cursor:pointer" onclick="openPlayer(\'' + q + '\')"><div style="font-weight:700;font-size:12px;margin-bottom:4px">' + esc(x.b.name) + ' <span style="color:var(--text-3);font-weight:400">' + posShort(x.b.position) + ' \u00b7 n=' + x.nwo + ' without</span></div><div class="lu-grid" style="gap:4px">' + chips + '</div></div>';
      }).join('');
      return '<div class="lc-card"><div class="lc-hd"><span class="lc-nm"><i class="ti ti-user-off"></i> ' + esc(c.star.name) + ' ' + esc(String(c.status || 'OUT').toUpperCase()) + '</span><span class="lc-meta">' + posShort(c.star.position) + ' \u00b7 ' + abbr(c.star.team) + (c.opp ? ' v ' + abbr(c.opp) : '') + (c.injury ? ' \u00b7 ' + esc(c.injury) : '') + '</span></div>' + rows + '</div>';
    }

    // ---- Stack Lab — QB + pass-catcher correlated stacks (copula joint via injected _pairRho/_clusterJoint) ----
    function _stackProb(name, statKey, line) {
      var hr = JTTScoring.getHitRate(name, statKey, line, true);
      if (hr && hr.n >= 6) return { prob: hr.rate, n: hr.n };
      var p = byName(name);
      return { prob: p ? estimateHitProb(p[statKey] || 0, line) : 0.5, n: 0 };
    }
    function stackLab() {
      var scope = getScope(), D = getData(), out = [];
      (D.fixture || []).forEach(function (g) {
        if (scope && !scope.has(g.home) && !scope.has(g.away)) return;
        [g.home, g.away].forEach(function (team) {
          var qb = playersOnTeam(team).filter(function (p) { return p.position === 'QB' && (p.matches || 0) >= 5 && (p.passYds || 0) >= 180; }).sort(function (a, b) { return (b.passYds || 0) - (a.passYds || 0); })[0];
          if (!qb) return;
          var qLine = Math.round(qb.passYds) - 20;
          var qp = _stackProb(qb.name, 'passYds', qLine);
          var qLeg = { p: qb, statKey: 'passYds', line: qLine, prob: qp.prob };
          playersOnTeam(team).filter(function (p) { return ['WR', 'TE', 'RB'].indexOf(p.position) >= 0 && (p.matches || 0) >= 5 && (p.tgtShare || 0) >= 15; }).sort(function (a, b) { return (b.tgtShare || 0) - (a.tgtShare || 0); }).slice(0, 3).forEach(function (w) {
            var opts = [];
            if ((w.recYds || 0) >= 40) opts.push(['recYds', Math.round(w.recYds) - 8, 'Rec Yds']);
            if ((w.receptions || 0) >= 3.5) opts.push(['receptions', Math.max(2, Math.floor(w.receptions) - 1), 'Receptions']);
            if (!opts.length) return;
            var best = null;
            opts.forEach(function (o) {
              var k = o[0], line = o[1], lab = o[2];
              var wp = _stackProb(w.name, k, line);
              var wLeg = { p: w, statKey: k, line: line, prob: wp.prob };
              var rho = _pairRho(qLeg, wLeg), joint = _clusterJoint([qLeg, wLeg]), indep = qLeg.prob * wLeg.prob;
              if (indep <= 0) return;
              var uplift = (joint - indep) / indep * 100;
              var cand = { qb: qb, qLine: qLine, w: w, k: k, lab: lab, line: line, rho: rho, joint: joint, indep: indep, uplift: uplift, team: team, opp: nextOpp(team), real: qp.n >= 6 && wp.n >= 6, score: joint * (1 + Math.max(0, uplift) / 100) };
              if (!best || cand.score > best.score) best = cand;
            });
            if (best && best.joint >= 0.25) out.push(best);
          });
        });
      });
      return out.sort(function (a, b) { return b.score - a.score; });
    }
    function stackCard(c) {
      var q1 = esc(c.qb.name).replace(/'/g, "\\'"), q2 = esc(c.w.name).replace(/'/g, "\\'");
      var upCol = c.uplift >= 8 ? '#22c55e' : c.uplift <= -5 ? '#f97316' : 'var(--text-3)';
      return '<div class="lc-card"><div class="lc-hd"><span class="lc-nm"><i class="ti ti-link"></i> ' + abbr(c.team) + ' stack</span><span class="lc-meta">' + (c.opp ? 'v ' + abbr(c.opp) + ' \u00b7 ' : '') + 'fair $' + (1 / c.joint).toFixed(2) + '</span></div>' +
        '<div class="dv-leg" style="cursor:pointer" onclick="openPlayer(\'' + q1 + '\')"><span class="dv-n">' + esc(c.qb.name) + '</span><span class="dv-l">' + c.qLine + '+ Pass Yds</span></div>' +
        '<div class="dv-leg" style="cursor:pointer" onclick="openPlayer(\'' + q2 + '\')"><span class="dv-n">' + esc(c.w.name) + '</span><span class="dv-l">' + c.line + '+ ' + c.lab + '</span></div>' +
        '<div class="lu-grid" style="gap:5px;margin-top:6px"><span class="lu-p" style="color:' + upCol + ';border-color:' + upCol + '55">correlated ' + Math.round(c.joint * 100) + '% v naive ' + Math.round(c.indep * 100) + '% (' + (c.uplift >= 0 ? '+' : '') + c.uplift.toFixed(0) + '%)</span><span class="lu-p">\u03c1 ' + c.rho.toFixed(2) + '</span>' + (c.real ? '' : '<span class="lu-p" style="color:#eab308;border-color:#eab30855">est \u00b7 thin shared history</span>') + '</div></div>';
    }

    // ---- Playoff Push — standings pressure (conference seeding from results.json) ----
    function _standings() {
      var res = (getData().results) || []; if (!res.length) return null;
      var played = function (s) { return res.filter(function (r) { return String(r.season) === String(s) && r.hs != null && r.as != null; }); };
      var season = +curSeason(), rows = played(season), usedPrev = false;
      if (rows.length < 16) {
        var seasons = Array.from(new Set(res.map(function (r) { return +r.season; }))).filter(Boolean).sort(function (a, b) { return b - a; });
        var s2 = seasons.find(function (s) { return played(s).length >= 32; });
        if (s2 == null) return null;
        usedPrev = s2 !== season; season = s2; rows = played(s2);
      }
      var rec = {};
      rows.forEach(function (r) {
        var add = function (t, w, l, ti) { var o = rec[t] = rec[t] || { w: 0, l: 0, t: 0 }; o.w += w; o.l += l; o.t += ti; };
        if (r.hs > r.as) { add(r.home, 1, 0, 0); add(r.away, 0, 1, 0); }
        else if (r.as > r.hs) { add(r.away, 1, 0, 0); add(r.home, 0, 1, 0); }
        else { add(r.home, 0, 0, 1); add(r.away, 0, 0, 1); }
      });
      var tm = teamMap(), conf = {};
      Object.keys(rec).forEach(function (t) {
        var o = rec[t], tmr = tm[t], c = (tmr && tmr.conference) || '\u2014', g = o.w + o.l + o.t;
        (conf[c] = conf[c] || []).push({ team: t, w: o.w, l: o.l, t: o.t, pct: g ? (o.w + 0.5 * o.t) / g : 0 });
      });
      Object.keys(conf).forEach(function (c) { conf[c].sort(function (x, y) { return (y.pct - x.pct) || (y.w - x.w); }); conf[c].forEach(function (r, i) { r.seed = i + 1; }); });
      return { conf: conf, season: season, usedPrev: usedPrev };
    }
    function playoffPush() {
      var st = _standings(); if (!st) return [];
      var teams = _fixtureSet(), out = [];
      Object.keys(st.conf).forEach(function (c) {
        var arr = st.conf[c], seven = arr[6]; if (!seven) return;
        arr.forEach(function (r) {
          if (!teams.has(r.team)) return;
          var gb = ((seven.w - r.w) + (r.l - seven.l)) / 2;
          if (!(r.seed >= 5 && r.seed <= 11 && gb <= 2.5)) return;
          var opp = nextOpp(r.team);
          var stars = playersOnTeam(r.team).filter(function (p) { return (p.matches || 0) >= 4 && isPlaying(p.name, r.team); }).sort(function (a, b) { return (b.fanPts || 0) - (a.fanPts || 0); }).slice(0, 3);
          out.push({ team: r.team, conf: c, seed: r.seed, recStr: r.w + '-' + r.l + (r.t ? '-' + r.t : ''), gb: Math.max(0, gb), opp: opp, stars: stars, season: st.season, usedPrev: st.usedPrev });
        });
      });
      return out.sort(function (a, b) { return (a.gb - b.gb) || (a.seed - b.seed); });
    }
    function pushCard(c) {
      var starRows = c.stars.map(function (p) {
        var mk = p.position === 'QB' ? 'passYds' : p.position === 'RB' ? 'rushYds' : 'recYds';
        var pct = c.opp ? JTTScoring.getDVPPct(c.opp, _dvpPos(p), mk) : null;
        var q = esc(p.name).replace(/'/g, "\\'");
        return '<div class="dv-leg" style="cursor:pointer" onclick="openPlayer(\'' + q + '\')"><span class="dv-n">' + esc(p.name) + '</span><span class="dv-l">' + posShort(p.position) + ' \u00b7 ' + fmt(p.fanPts, 1) + ' FP' + (pct != null ? ' \u00b7 dvp ' + (pct > 0 ? '+' : '') + pct.toFixed(0) + '%' : '') + '</span><span class="dv-h">' + (tdOddsTag(p.name).replace(/^ \u00b7 /, '') || '') + '</span></div>';
      }).join('');
      var gbStr = c.gb === 0 ? 'holds the 7 seed line' : (c.gb + ' GB of the 7 seed');
      return '<div class="lc-card"><div class="lc-hd"><span class="lc-nm">' + teamLogo(c.team, 20) + ' ' + abbr(c.team) + '</span><span class="lc-meta">' + esc(c.conf) + ' #' + c.seed + ' \u00b7 ' + c.recStr + ' \u00b7 ' + gbStr + (c.opp ? ' \u00b7 v ' + abbr(c.opp) : '') + (c.usedPrev ? ' \u00b7 ' + c.season + ' standings' : '') + '</span></div>' + starRows + '</div>';
    }

    // ---- Clamp Watch — shadow-CB coverage risk (data/dbs.json; null => feed-pending state) ----
    function clampWatch() {
      var D = getData(), dbs = D.dbs || []; if (!dbs.length) return null;
      var byTeam = {}; dbs.forEach(function (r) { if (r && r.team) (byTeam[r.team] = byTeam[r.team] || []).push(r); });
      var scope = getScope(), out = [];
      (D.fixture || []).forEach(function (g) {
        if (scope && !scope.has(g.home) && !scope.has(g.away)) return;
        [[g.home, g.away], [g.away, g.home]].forEach(function (pair) {
          var myTeam = pair[0], oppTeam = pair[1];
          var cbs = byTeam[oppTeam]; if (!cbs || !cbs.length) return;
          playersOnTeam(myTeam).filter(function (p) { return p.position === 'WR' && (p.tgtShare || 0) >= 20 && isPlaying(p.name, myTeam); }).forEach(function (wr) {
            cbs.forEach(function (cb) { out.push({ threat: wr, db: cb, oppTeam: oppTeam, type: cb.role || 'shadow' }); });
          });
        });
      });
      return out;
    }

    // ---- Tackle Machines — combined tackles clearance + tackle-funnel matchup ----
    var WRAP_HIT = 0.65, WRAP_MIN_G = 6, WRAP_STARVE = -15;
    function _oppVolume(opp) {
      var tm = teamMap();
      var arr = Object.keys(tm).map(function (k) { return tm[k]; }).filter(function (t) { return t.plays != null; });
      if (!arr.length || !tm[opp]) return null;
      var t = tm[opp];
      var pr = arr.slice().sort(function (a, b) { return (b.plays || 0) - (a.plays || 0); }).findIndex(function (x) { return x.team === opp; }) + 1;
      var rr = arr.slice().sort(function (a, b) { return (b.rushAtt || 0) - (a.rushAtt || 0); }).findIndex(function (x) { return x.team === opp; }) + 1;
      return { plays: t.plays, playsRank: pr, rushAtt: t.rushAtt, rushRank: rr, n: arr.length };
    }
    function tackleMachines() {
      var teams = _fixtureSet(), out = [];
      (players || []).filter(function (p) { return teams.has(p.team) && DEF_POS.has(p.position) && (p.matches || 0) >= 4 && (p.snapPct || 0) >= 60; }).forEach(function (p) {
        var opp = nextOpp(p.team);
        var vals = (logsFor(p.name) || []).map(function (r) { return r.tackles; }).filter(function (v) { return v != null; });
        if (vals.length < WRAP_MIN_G) return;
        var avg = _avg(vals); if (avg < 4.5) return;
        var posted = oddsFor(p.name, 'tackles');
        var line = (posted && posted.line != null) ? posted.line : Math.max(4, Math.floor(avg) - 1) - 0.5;
        var hits = vals.filter(function (v) { return v > line; }).length, rate = hits / vals.length;
        if (rate < WRAP_HIT) return;
        var funnel = opp ? JTTScoring.getDVPPct(opp, p.position, 'tackles') : null;
        if (funnel != null && funnel <= WRAP_STARVE) return;   // opp starves this position of tackle chances
        var vol = opp ? _oppVolume(opp) : null;
        var l5 = vals.slice(-5).map(function (v) { return Math.round(v); }).join(', ');
        var score = rate * 100 + (funnel || 0) * 0.7 + (vol && vol.playsRank <= 8 ? 6 : 0) + (vol && vol.rushRank <= 8 && p.position !== 'DB' ? 6 : 0) + ((p.snapPct || 0) >= 90 ? 4 : 0);
        out.push({ p: p, opp: opp, line: line, hits: hits, n: vals.length, rate: rate, avg: avg, funnel: funnel, vol: vol, l5: l5, score: score, posted: !!(posted && posted.line != null) });
      });
      return out.sort(function (a, b) { return b.score - a.score; });
    }
    function tackleCard(c) {
      var q = esc(c.p.name).replace(/'/g, "\\'"), od = lineTag(c.p.name, 'tackles'), chips = [];
      if (c.funnel != null) { var col = c.funnel >= 10 ? '#22c55e' : c.funnel >= 3 ? '#86efac' : c.funnel <= -6 ? '#f97316' : 'var(--text-3)';
        chips.push('<span class="lu-p" style="color:' + col + ';border-color:' + col + '55">' + abbr(c.opp) + ' offence feeds ' + (c.funnel >= 0 ? '+' : '') + c.funnel.toFixed(0) + '% ' + posShort(c.p.position) + ' tackles</span>'); }
      if (c.vol) { chips.push('<span class="lu-p">' + c.vol.plays.toFixed(0) + ' plays/g (#' + c.vol.playsRank + '/' + c.vol.n + ')</span>');
        if (c.p.position !== 'DB') chips.push('<span class="lu-p">' + c.vol.rushAtt.toFixed(0) + ' rush att/g (#' + c.vol.rushRank + ')</span>'); }
      if ((c.p.snapPct || 0) >= 90) chips.push('<span class="lu-p" style="color:#22c55e;border-color:#22c55e55">every-down \u00b7 ' + c.p.snapPct.toFixed(0) + '% snaps</span>');
      return '<div class="lc-card" onclick="openPlayer(\'' + q + '\')"><div class="lc-hd"><span class="lc-nm">' + esc(c.p.name) + '</span>' + _degBadges(c.p.name) + '<span class="lc-meta">' + posShort(c.p.position) + ' \u00b7 ' + abbr(c.p.team) + (c.opp ? ' v ' + abbr(c.opp) : '') + (od ? ' \u00b7' + od : '') + '</span></div><div class="tp-body-meta" style="border:0;padding:2px 0 6px"><b>Tackles+Ast ' + c.line + '+</b>' + (c.posted ? '' : ' (est line)') + ' \u00b7 hit ' + c.hits + '/' + c.n + ' (' + Math.round(c.rate * 100) + '%) \u00b7 avg ' + c.avg.toFixed(1) + ' \u00b7 L5 ' + c.l5 + '</div>' + (chips.length ? '<div class="lu-grid" style="gap:5px">' + chips.join(' ') + '</div>' : '') + '</div>';
    }

    // tiles: key -> () => HTML. The shell's renderTile(k) calls tiles[k]() for NFL.
    // Keys mirror the shell's tile vocabulary. (More ported in batches.)
    var M = function (v) { return fmt(v, 1); };
    // ===== Signal Ledger — every signal graded chronologically against what actually happened =====
    var _ledgerCache = null;
    function _lgAvg(funnel, pos) {
      var vs = Object.keys(funnel).filter(function (k) { return k.endsWith('|' + pos); }).map(function (k) { return funnel[k].sum / Math.max(1, funnel[k].g.size); });
      return vs.length ? vs.reduce(function (s, v) { return s + v; }, 0) / vs.length : 1;
    }
    var LEDGER_LABELS = {
      streak: ['Streakers', '7-game streaks continuing at the realistic line'],
      chunk: ['Chunk Plays', 'longest rec/rush/comp clearing the market-typical gate'],
      wrap: ['Tackle Machines', 'tackles+assists clearing the est line, funnel-gated'],
      tuddy: ['Tuddy Targets', 'TD-rate players scoring v a running bottom-10 TD defence']
    };
    function _ledgerCompute() {
      if (_ledgerCache) return _ledgerCache;
      try { var st = JSON.parse(localStorage.getItem('jtt_nfl_ledger') || 'null'); if (st && st.v === state.dataVersion && st.rows) { _ledgerCache = st; return st; } } catch (e) { }
      var logs = (state.data.gamelogs || []);
      var posOf = function (n) { var p = state.idx.byName[n]; return p ? p.position : null; };
      var byWk = {};
      logs.forEach(function (r) { var k = (+r.Year) * 100 + (+r.Week || 0); (byWk[k] = byWk[k] || []).push(r); });
      var weeks = Object.keys(byWk).map(Number).sort(function (a, b) { return a - b; });
      var hist = {}, funnel = {}, tdAllowed = {};
      var S = { streak: { n: 0, h: 0, r8: [] }, chunk: { n: 0, h: 0, r8: [] }, wrap: { n: 0, h: 0, r8: [] }, tuddy: { n: 0, h: 0, r8: [] } };
      weeks.forEach(function (wk) {
        var rows = byWk[wk];
        rows.forEach(function (r) {
          var h = hist[r.Player]; if (!h || h.length < 6) return;
          var pos = posOf(r.Player); if (!pos) return;
          var grade = function (sig, hit) { S[sig].n++; if (hit) S[sig].h++; S[sig].r8.push({ wk: wk, hit: hit ? 1 : 0 }); };
          for (var si = 0; si < STREAK_STATS.length; si++) {
            var k = STREAK_STATS[si][0];
            var vals = h.map(function (x) { return x[k] || 0; });
            var avg = vals.reduce(function (s, v) { return s + v; }, 0) / vals.length;
            var line = realisticLine2(k, avg); if (line == null) continue;
            var stk = 0; for (var i = vals.length - 1; i >= 0; i--) { if (vals[i] >= line) stk++; else break; }
            if (stk >= 7) grade('streak', (r[k] || 0) >= line);
          }
          CHUNK_DEFS.forEach(function (def) {
            if (def.pos.indexOf(pos) < 0) return;
            var vals = h.map(function (x) { return x[def.k]; }).filter(function (v) { return v != null; });
            if (vals.length < 6 || r[def.k] == null) return;
            var rate = vals.filter(function (v) { return v >= def.thr; }).length / vals.length;
            if (rate >= CHUNK_HIT) grade('chunk', r[def.k] >= def.thr);
          });
          if (DEF_POS.has(pos)) {
            var tv = h.map(function (x) { return x.tackles; }).filter(function (v) { return v != null; });
            if (tv.length >= 6) {
              var tavg = tv.reduce(function (s, v) { return s + v; }, 0) / tv.length;
              if (tavg >= 4.5) {
                var tline = Math.max(4, Math.floor(tavg) - 1) - 0.5;
                var trate = tv.filter(function (v) { return v > tline; }).length / tv.length;
                var f = funnel[r.Opp + '|' + pos];
                var fPct = f && f.g.size >= 4 ? ((f.sum / f.g.size) / _lgAvg(funnel, pos) - 1) * 100 : null;
                if (trate >= WRAP_HIT && (fPct == null || fPct > WRAP_STARVE)) grade('wrap', (r.tackles || 0) > tline);
              }
            }
          }
          if (['RB', 'WR', 'TE', 'QB'].indexOf(pos) >= 0) {
            var dv = h.map(function (x) { return x.totalTds || 0; });
            var tdr = dv.reduce(function (s, v) { return s + v; }, 0) / dv.length;
            if (tdr >= 0.5) {
              var ranks = Object.keys(tdAllowed).filter(function (k2) { return k2.endsWith('|' + pos) && tdAllowed[k2].g.size >= 4; })
                .map(function (k2) { return { t: k2.split('|')[0], v: tdAllowed[k2].td / tdAllowed[k2].g.size }; })
                .sort(function (a, b) { return b.v - a.v; });
              var idx = -1; for (var ri = 0; ri < ranks.length; ri++) { if (ranks[ri].t === r.Opp) { idx = ri; break; } }
              if (idx >= 0 && idx < 10) grade('tuddy', (r.totalTds || 0) >= 1);
            }
          }
        });
        rows.forEach(function (r) {
          (hist[r.Player] = hist[r.Player] || []).push(r);
          var pos = posOf(r.Player); if (!pos) return;
          if (DEF_POS.has(pos) && r.tackles != null) { var f = funnel[r.Opp + '|' + pos] = funnel[r.Opp + '|' + pos] || { sum: 0, g: new Set() }; f.sum += r.tackles; f.g.add(r.MatchId); }
          if (['RB', 'WR', 'TE', 'QB'].indexOf(pos) >= 0) { var t = tdAllowed[r.Opp + '|' + pos] = tdAllowed[r.Opp + '|' + pos] || { td: 0, g: new Set() }; t.td += (r.totalTds || 0); t.g.add(r.MatchId); }
        });
      });
      var lastWk = weeks[weeks.length - 1] || 0;
      var rowsOut = Object.keys(S).map(function (k) {
        var v = S[k]; var l8 = v.r8.filter(function (x) { return x.wk > lastWk - 8; });
        return { sig: k, n: v.n, h: v.h, pct: v.n ? v.h / v.n * 100 : 0, n8: l8.length, h8: l8.reduce(function (s, x) { return s + x.hit; }, 0) };
      });
      var res = { v: state.dataVersion, rows: rowsOut, weeks: weeks.length };
      try { localStorage.setItem('jtt_nfl_ledger', JSON.stringify(res)); } catch (e) { }
      _ledgerCache = res; return res;
    }
    function ledgerPage() {
      var L = _ledgerCompute();
      if (!L || !L.rows || !L.rows.some(function (r) { return r.n; })) return emptyState('ti-notebook', 'Ledger needs history', 'Signal grading builds from completed game weeks.');
      var h = '<div class="tp-body-meta" style="border:0;margin-bottom:10px">Every signal condition replayed chronologically over ' + L.weeks + ' data weeks \u2014 evaluated with only the history available at the time, graded against what actually happened. Hit rates are at <b>estimated lines</b>; posted-line grading, CLV and units land with the live odds feed.</div>';
      h += '<div class="stbl-wrap"><table class="stbl bx"><thead><tr><th>Signal</th><th>Graded</th><th>Hit</th><th>Hit %</th><th>Last 8 wks</th></tr></thead><tbody>';
      L.rows.slice().sort(function (a, b) { return b.pct - a.pct; }).forEach(function (r) {
        var lab = LEDGER_LABELS[r.sig] || [r.sig, ''];
        var col = r.pct >= 62 ? '#22c55e' : r.pct >= 52 ? '#eab308' : '#ef4444';
        h += '<tr><td class="name" title="' + esc(lab[1]) + '">' + esc(lab[0]) + '</td><td>' + r.n + '</td><td>' + r.h + '</td>' +
          '<td style="color:' + col + ';font-weight:700">' + r.pct.toFixed(1) + '%</td>' +
          '<td>' + (r.n8 ? (r.h8 + '/' + r.n8 + ' (' + Math.round(r.h8 / r.n8 * 100) + '%)') : '\u2014') + '</td></tr>';
      });
      h += '</tbody></table></div>';
      h += '<div class="tp-body-meta" style="border:0;margin-top:8px">Method notes: no look-ahead \u2014 a Week 9 signal only knew Weeks 1\u20138. Chunk and Tackle grades use the same qualification gates as the live tiles; Tuddy uses the TD-rate \u00d7 soft-defence core. Streakers grade continuation of the streak.</div>';
      return h;
    }
    // ===== College-native Degen engines (ported from the standalone NCAAF tool) =====
    function _ncOpen(n){ return "openPlayer('"+String(n||'').replace(/'/g,"\\'")+"')"; }
    function _ncPos(p){ return posShort(p.position||p.pos||''); }
    // Tempo Kings — top plays-per-game teams, their volume players get more snaps
    function tempoKings(){
      var fs=_fixtureSet(), tm=teamMap||{};
      var all=Object.keys(tm).map(function(k){return tm[k];}).filter(function(t){return t.plays!=null&&(t.matches||0)>=2;});
      if(all.length<20) return [];
      var ranked=all.slice().sort(function(a,b){return (b.plays||0)-(a.plays||0);});
      var cut=Math.max(3,Math.ceil(ranked.length*0.12)), out=[];
      ranked.slice(0,cut).forEach(function(t,i){
        if(!fs.has(t.team)) return; var opp=nextOpp(t.team); if(!opp) return;
        var to=tm[opp]||{};
        var vols=(playersOnTeam(t.team)||[]).filter(function(p){return (p.matches||0)>=3&&(p.touchShare||0)>=12;})
          .sort(function(a,b){return (b.touchShare||0)-(a.touchShare||0);}).slice(0,3);
        out.push({team:t.team,opp:opp,rank:i+1,plays:t.plays,oppAllows:to.plays_a||null,vols:vols});
      });
      return out.sort(function(a,b){return a.rank-b.rank;});
    }
    function tempoCard(c){
      var rows=(c.vols||[]).map(function(p){
        return '<div style="padding:5px 0;border-top:1px solid var(--line);cursor:pointer;display:flex;justify-content:space-between" onclick="'+_ncOpen(p.name)+'">'+
          '<span style="font-weight:700;font-size:12px">'+esc(p.name)+' <span style="color:var(--text-3);font-weight:400">'+_ncPos(p)+'</span></span>'+
          '<span style="font-family:var(--font-mono);font-size:11px;color:var(--text-2)">'+fmt(p.touchShare,0)+'% touch</span></div>';
      }).join('');
      return '<div class="lc-card"><div class="lc-hd"><span class="lc-nm"><i class="ti ti-gauge"></i> '+esc(abbr(c.team))+'</span>'+
        '<span class="lc-meta">'+fmt(c.plays,1)+' plays/g \u00b7 v '+esc(abbr(c.opp))+(c.oppAllows?' (allow '+fmt(c.oppAllows,1)+')':'')+'</span></div>'+rows+'</div>';
    }
    // Dual Threat — QBs whose legs are a second prop market
    function dualThreat(){
      var fs=_fixtureSet(), out=[];
      (players||[]).filter(function(p){return fs.has(p.team)&&(p.position==='QB')&&(p.matches||0)>=3;}).forEach(function(p){
        var ry=p.rushYds||0, ra=p.rushAtt||0;
        if(!(ry>=30||(ra>=6&&ry>=20))) return;
        var opp=nextOpp(p.team); if(!opp) return;
        var dvpPct=(JTTScoring&&JTTScoring.getDVPPct)?JTTScoring.getDVPPct(opp,'QB','rushYds'):null;
        out.push({p:p,opp:opp,ry:ry,ra:ra,ypc:p.ypc||(ra>0?ry/ra:0),dvpPct:dvpPct});
      });
      return out.sort(function(a,b){return b.ry-a.ry;});
    }
    function dualCard(c){
      return '<div class="lc-card"><div class="lc-hd"><span class="lc-nm" style="cursor:pointer" onclick="'+_ncOpen(c.p.name)+'"><i class="ti ti-arrows-split-2"></i> '+esc(c.p.name)+'</span>'+
        '<span class="lc-meta">'+esc(abbr(c.p.team))+' v '+esc(abbr(c.opp))+' \u00b7 '+fmt(c.ry,0)+' ru yds/g \u00b7 '+fmt(c.ra,1)+' car'+(c.dvpPct!=null?' \u00b7 opp '+(c.dvpPct>0?'+':'')+Math.round(c.dvpPct)+'% v QB rush':'')+'</span></div></div>';
    }
    // Freshman Watch — FR/SO with a rising touch share (L3 vs season)
    function freshmanWatch(){
      var fs=_fixtureSet(), out=[];
      (players||[]).filter(function(p){return fs.has(p.team)&&(p.classYear==='FR'||p.classYear==='SO')&&(p.matches||0)>=3;}).forEach(function(p){
        var logs=(logsFor(p.name)||[]); if(logs.length<3) return;
        var ts=function(r){return +r.touchShare||0;};
        var l3=logs.slice(-3), av=function(a){return a.reduce(function(s,r){return s+ts(r);},0)/a.length;};
        var l3s=av(l3), ses=av(logs), d=l3s-ses;
        if(d<3||l3s<8) return;
        out.push({p:p,opp:nextOpp(p.team),l3:l3s,season:ses,delta:d});
      });
      return out.sort(function(a,b){return b.delta-a.delta;});
    }
    function froshCard(c){
      return '<div class="lc-card"><div class="lc-hd"><span class="lc-nm" style="cursor:pointer" onclick="'+_ncOpen(c.p.name)+'"><i class="ti ti-seeding"></i> '+esc(c.p.name)+' <span style="color:var(--text-3);font-weight:400">'+esc(c.p.classYear)+'</span></span>'+
        '<span class="lc-meta">'+esc(abbr(c.p.team))+(c.opp?' v '+esc(abbr(c.opp)):'')+' \u00b7 L3 '+fmt(c.l3,0)+'% touch (+'+Math.round(c.delta)+' vs season)</span></div></div>';
    }
    // Garbage Time — calibrated blowout risk
    function _calibFor(sp){
      var C=(getData().calib)||[]; var a=Math.abs(+sp||0); if(!C.length) return null;
      for(var i=0;i<C.length;i++){ var r=C[i]; if(a>=r.spreadLo&&(a<r.spreadHi||i===C.length-1)) return r; }
      return null;
    }
    function garbageTime(){
      var scope=getScope(), D=getData(), out=[];
      (D.fixture||[]).forEach(function(g){
        if(scope && !scope.has(g.home) && !scope.has(g.away)) return;
        if(g.spread==null||Math.abs(g.spread)<14) return;
        var cal=_calibFor(g.spread); if(!cal||(cal.pBlowout17||0)<0.45) return;
        var fav=g.spread<0?g.home:g.away, dog=fav===g.home?g.away:g.home;
        var stars=(playersOnTeam(fav)||[]).filter(function(p){return (p.matches||0)>=3&&(p.touchShare||0)>=15;})
          .sort(function(a,b){return (b.touchShare||0)-(a.touchShare||0);}).slice(0,2);
        out.push({fav:fav,dog:dog,spread:g.spread,p17:cal.pBlowout17,p24:cal.pBlowout24,med:cal.medianMargin,stars:stars});
      });
      return out.sort(function(a,b){return b.p17-a.p17;});
    }
    function garbageCard(c){
      var pl=(c.stars||[]).map(function(p){return '<span class="lu-p" style="cursor:pointer" onclick="'+_ncOpen(p.name)+'">'+esc(p.name)+' '+fmt(p.touchShare,0)+'%</span>';}).join(', ');
      return '<div class="lc-card"><div class="lc-hd"><span class="lc-nm"><i class="ti ti-trash"></i> '+esc(abbr(c.fav))+' -'+Math.abs(c.spread).toFixed(1)+'</span>'+
        '<span class="lc-meta">'+Math.round(c.p17*100)+'% land 17+ \u00b7 '+Math.round((c.p24||0)*100)+'% land 24+ \u00b7 median '+fmt(c.med,0)+'</span></div>'+
        (pl?'<div style="padding:6px 0;border-top:1px solid var(--line)"><div class="deg-sub" style="margin-bottom:4px">Starters at 4th-Q minutes risk</div>'+pl+'</div>':'')+'</div>';
    }
    // Trap Watch — ranked chalk in a letdown spot
    function _lastMargin(team){
      var best=null;
      ((getData().results)||[]).forEach(function(r){
        if(r.hs==null||r.as==null) return; if(r.home!==team&&r.away!==team) return;
        var k=String(r.date||''); if(best&&k<=best.k) return;
        best={k:k,m:(r.home===team?r.hs-r.as:r.as-r.hs)};
      });
      return best?best.m:null;
    }
    function trapWatch(){
      var scope=getScope(), out=[];
      (getData().fixture||[]).forEach(function(g){
        if(scope && !scope.has(g.home) && !scope.has(g.away)) return;
        if(g.spread==null||Math.abs(g.spread)<10) return;
        var fav=g.spread<0?g.home:g.away, dog=fav===g.home?g.away:g.home;
        var favRank=fav===g.home?g.homeRank:g.awayRank, dogRank=fav===g.home?g.awayRank:g.homeRank;
        if(!favRank||dogRank) return;
        var lm=_lastMargin(fav); if(lm==null||lm<21) return;
        var star=(playersOnTeam(fav)||[]).filter(function(p){return (p.matches||0)>=3;})
          .sort(function(a,b){return (b.touchShare||0)-(a.touchShare||0);})[0]||null;
        out.push({fav:fav,dog:dog,favRank:favRank,spread:g.spread,lastMargin:lm,star:star});
      });
      return out.sort(function(a,b){return b.lastMargin-a.lastMargin;});
    }
    function trapCard(c){
      return '<div class="lc-card"><div class="lc-hd"><span class="lc-nm"><i class="ti ti-alert-triangle"></i> #'+c.favRank+' '+esc(abbr(c.fav))+' -'+Math.abs(c.spread).toFixed(1)+'</span>'+
        '<span class="lc-meta">won last by '+c.lastMargin+' \u00b7 unranked dog \u00b7 letdown shape</span></div>'+
        '<div style="padding:6px 0;border-top:1px solid var(--line)"><div class="deg-sub">Dog +'+Math.abs(c.spread).toFixed(1)+' and dog-side volume overs are the value'+(c.star?' \u2014 fade '+esc(c.star.name):'')+'.</div></div></div>';
    }
    function _ncWrap(icon,title,rows,cardFn,cls,empty){
      var arr=(rows||[]); if(!arr.length) return isFocused()?'':emptyState(icon,title+' is quiet',empty);
      return degWrap(icon,title,arr.slice(0,12).map(cardFn),cls||'c-soft');
    }


    var tiles = {
      tempo:  function(){ return _ncWrap('ti-gauge','Tempo Kings',tempoKings(),tempoCard,'c-soft','No fast-tempo teams on the slate.'); },
      dual:   function(){ return _ncWrap('ti-arrows-split-2','Dual Threat',dualThreat(),dualCard,'c-soft','No dual-threat QBs on the slate.'); },
      frosh:  function(){ return _ncWrap('ti-seeding','Freshman Watch',freshmanWatch(),froshCard,'c-soft','No rising freshmen on the slate.'); },
      garbage:function(){ return _ncWrap('ti-trash','Garbage Time',garbageTime(),garbageCard,'c-red','No blowout scripts on the slate.'); },
      trap:   function(){ return _ncWrap('ti-alert-triangle','Trap Watch',trapWatch(),trapCard,'c-red','No trap spots on the slate.'); },
      ledger: function () { return ledgerPage(); },
      next: function () {
        var arr = _fc(nextManUp(), 6, 30);
        if (!arr.length) return isFocused() ? '' : emptyState('ti-user-plus', 'Next Man Up is quiet', 'No meaningful starter on the slate is listed Out/Doubtful. Alerts fire from the injury report as it fills through the week.');
        return degWrap('ti-user-plus', 'Next Man Up', arr.map(nextCard), 'c-amber');
      },
      stack: function () {
        var rows = _fc(stackLab(), 6, 30).map(stackCard);
        return degWrap('ti-link', 'Stack Lab', rows, 'c-cyan');
      },
      push: function () {
        var rows = _fc(playoffPush(), 6, 20).map(pushCard);
        var wrap = degWrap('ti-ladder', 'Playoff Push', rows, 'c-green');
        var st = _standings();
        return (st && st.usedPrev && rows.length ? '<div class="tp-body-meta" style="border:0;margin:4px 0 0">Standings from the ' + st.season + ' season \u2014 updates live once ' + curSeason() + ' results land.</div>' : '') + wrap;
      },
      clamp: function () {
        var arr = clampWatch();
        if (arr === null) return isFocused() ? '' : emptyState('ti-lock', 'Clamp Watch is arming', 'Corner coverage grades (PFR advanced defense) land with the next data build \u2014 the engine is wired and waiting.');
        var rows = _byGame(_fc(arr, 8), function (t) { return t.threat.team; }, function (t) {
          return degRow(t.threat.name, '#a855f7', t.type === 'shadow' ? 'SHADOW CB risk' : 'Top CB risk',
            t.threat.position + ' \u00b7 ' + abbr(t.threat.team) + ' \u2014 likely ' + t.db.player + ' (' + abbr(t.oppTeam) + ')' + (t.db.grade ? ' \u00b7 ' + t.db.grade + ' rating allowed' : '') + (t.db.cmpPct ? ' \u00b7 ' + t.db.cmpPct.toFixed(0) + '% cmp' : '') + (t.threat.tgtShare ? ' \u00b7 ' + t.threat.tgtShare.toFixed(0) + '% tgt share' : ''), t.threat.name);
        });
        return degWrap('ti-lock', 'Clamp Watch', rows);
      },
      wrap: function () {
        var rows = _byGame(_fc(tackleMachines(), 6, 30), function (c) { return c.p.team; }, tackleCard);
        return degWrap('ti-hammer', 'Tackle Machines', rows, 'c-purple');
      },
      streak: function () {
        var rows = _byGame(_fc(streakers(), 6), function (s) { return s.p.team; }, function (s) {
          return degRow(s.p.name, '#f97316', { v1: s.streak, l1: 'straight', v2: Math.round(s.rate * 100) + '%', l2: 'hit' },
            s.stat + ' ' + s.line + '+ \u00b7 ' + abbr(s.p.team) + (s.opp ? ' v ' + abbr(s.opp) : '') + ' \u00b7 hit ' + Math.round(s.rate * 100) + '%' + (s.dvpPct != null ? ' \u00b7 ' + muTag(s.dvpPct) : '') + streakOdds(s));
        });
        return degWrap('ti-flame', 'Streakers', rows, 'c-amber');
      },
      form: function () {
        var up = _byGame(_fc(formAlerts('spike'), 6, 25), function (a) { return a.p.team; }, function (a) { return degRow(a.p.name, '#22c55e', { v1: '\u25b2 +' + Math.round(a.swing * 100) + '%', l1: 'swing', v2: M(a.l3), l2: 'L3' }, a.stat + ' \u00b7 L3 ' + M(a.l3) + ' vs season ' + M(a.seasonAvg) + formNote('spike', a.runDvp, a.thisDvp)); });
        var dn = _byGame(_fc(formAlerts('drop'), 6, 25), function (a) { return a.p.team; }, function (a) { return degRow(a.p.name, '#ef4444', { v1: '\u25bc ' + Math.round(a.swing * 100) + '%', l1: 'swing', v2: M(a.l3), l2: 'L3' }, a.stat + ' \u00b7 L3 ' + M(a.l3) + ' vs season ' + M(a.seasonAvg) + formNote('drop', a.runDvp, a.thisDvp)); });
        return degWrap('ti-trending-up', 'Spiking', up, 'c-green') + degWrap('ti-trending-down', 'Cooling', dn, 'c-red');
      },
      usage: function () {
        var rows = _byGame(_fc(usageTrend(), 8, 30), function (c) { return c.p.team; }, usageCard);
        return degWrap('ti-activity', 'Usage Trend', rows, 'c-green');
      },
      chunk: function () {
        var rows = _byGame(_fc(chunkPlays(), 6, 30), function (c) { return c.p.team; }, chunkCard);
        return degWrap('ti-bolt', 'Chunk Plays', rows, 'c-cyan');
      },
      bogey: function () {
        var rows = _byGame(_fc(bogey(), 6), function (b) { return b.p.team; }, function (b) {
          var sub = b.stat.l + ' \u00b7 vs ' + abbr(b.opp) + ' ' + M(b.thisAvg) + (b.worst != null ? ' (next worst ' + M(b.worst) + ')' : '') + ' \u00b7 ' + b.games + ' H2H' + underTag(b.p.name, b.stat.k);
          return degRow(b.p.name, '#ef4444', { v1: M(b.thisAvg), l1: b.stat.l, v2: (b.lineBogey ? '0/' + b.games : (b.diffPct != null ? '-' + b.diffPct.toFixed(0) + '%' : '\u2014')), l2: (b.lineBogey ? 'cleared' : 'v field') }, sub);
        });
        return degWrap('ti-mood-sad', 'Bogey', rows, 'c-red');
      },
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

    // Play-style classifier for Defence-vs-Play-Style (shell's allPlayStyles delegates here for NFL).
    function playStyles(p, pg) {
      var pa = p.passAtt || 0, py = p.passYds || 0, ra = p.rushAtt || 0, tg = p.targets || 0, rc = p.receptions || 0,
        ts = p.tgtShare || 0, adot = p.aDot || 0, ypr = p.ypr || 0, snap = p.snapPct || 0, gl = p.glCarry || 0,
        rtd = p.rushTds || 0, ttd = p.totalTds || 0, fp = p.fanPts || 0, touch = ra + tg;
      var s = [];
      if (pg === 'QB') {
        if (ra >= 5) s.push('Dual Threat');
        if (pa >= 36 || (adot >= 8.5 && py >= 240)) s.push('Gunslinger');
        if (pa > 0 && pa < 30) s.push('Game Manager');
        if (!s.length && pa >= 15) s.push('Pocket Passer');
      }
      if (pg === 'RB') {
        if (ra >= 15) s.push('Workhorse');
        if (ra > 0 && ra < 10) s.push('Committee Back');
        if (tg >= 4) s.push('Receiving Back');
        if (gl >= 1 || (rtd >= 0.5 && ra < 12)) s.push('Goal-Line Back');
      }
      if (pg === 'WR') {
        if (ts >= 24) s.push('Alpha WR');
        if (adot >= 13) s.push('Deep Threat');
        if (ypr > 0 && ypr < 11 && rc >= 4) s.push('Possession / Slot');
        if (snap > 0 && snap < 50) s.push('Rotational WR');
      }
      if (pg === 'TE') {
        if (ts >= 15) s.push('Route TE');
        else if (tg >= 2) s.push('Hybrid TE');
        else s.push('Blocking TE');
      }
      if (ttd >= 0.6) s.push('TD Machine');
      if (touch >= 18) s.push('Volume King');
      if (fp >= 15) s.push('Fantasy Stars');
      return s.length ? s : [pg];
    }

    return { collectOU: collectOU, matchupLegs: matchupLegs, tiles: tiles, playStyles: playStyles,
             captureOU: captureOU, captureMatchup: captureMatchup };
  }

  return { create: create };
});
