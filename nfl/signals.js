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

    // Capture helpers exist for API parity with AFL/signals.js. The live shell never calls
    // them (only the offline ledger job does); NFL's ledger pipeline can flesh these out later.
    function captureOU() { return []; }
    function captureMatchup() { return []; }

    return { collectOU: collectOU, matchupLegs: matchupLegs,
             captureOU: captureOU, captureMatchup: captureMatchup };
  }

  return { create: create };
});
