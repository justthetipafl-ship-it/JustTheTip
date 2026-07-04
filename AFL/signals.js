/* ============================================================================
 * JTT — signals.js  (shared signal module: browser + Node)
 * ----------------------------------------------------------------------------
 * ONE code path for every fired signal. The browser (index.html) and the
 * pre-slate capture job (Node, in the GitHub Actions pipeline) both import THIS
 * file, so the ledger grades the exact model the subscriber sees on screen.
 * Re-implementing signal logic in Python would drift — and a ledger that grades
 * a different model than it displays destroys the whole "receipts, not vibes"
 * honesty claim. So the logic lives here, once.
 *
 *   Browser:  <script src="signals.js"></script>  ->  window.JTTSignals
 *   Node:     const JTTSignals = require('./signals.js');
 *
 * The generators need tool-internal helpers (oddsFor, nextOpp, JTTScoring, …),
 * so this module is a DEPENDENCY-INJECTION FACTORY: callers build the deps and
 * call JTTSignals.create(deps) -> { collectOU, ... }. The browser injects its
 * live runtime helpers; the Node capture job injects equivalents built from the
 * frozen data + odds snapshot.
 * ========================================================================== */
(function (root, factory) {
  var mod = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = mod;   // Node
  root.JTTSignals = mod;                                                        // browser
})(typeof self !== 'undefined' ? self : (typeof globalThis !== 'undefined' ? globalThis : this), function () {
  'use strict';

  /* ============================================================================
   * CANONICAL SETTLEABLE-SIGNAL RECORD
   * ----------------------------------------------------------------------------
   * Every signal we intend to grade is normalised to this shape and FROZEN
   * pre-slate into signals/<season>-R<round>.json. The settle step reads box
   * scores and fills the result fields; it NEVER recomputes the signal, because
   * the live inputs (form, lines) have moved on by then.
   *
   * {
   *   id,          // stable: season|R<round>|signalType|player|market|line|side
   *   signalType,  // 'green_light' | 'death_rider' | 'snags' | 'streakers' | …
   *   season,      // '2026'
   *   round,       // 16   (numeric round key from _roundNum)
   *   capturedAt,  // ISO 8601, pre-slate
   *   player, team, opp,
   *   market,      // 'disposals' | 'goals' | 'tackles' | …
   *   line,        // the number graded against (the CAPTURED line, never re-derived)
   *   lineType,    // 'twoway' (X.5, no push) | 'milestone' (X+, >= semantics)
   *   side,        // 'over' | 'under'
   *   price,       // decimal odds at capture (null for an unpriced signal)
   *   book,        // best-book key at capture
   *   score,       // engine score at capture (context/debug, not graded)
   *   closePrice,  // filled at close for CLV (null until the close snapshot)
   *   // ---- filled by the settle step once results land ----
   *   result,      // 'win' | 'loss' | 'push' | 'void'   (void = DNP / scratch / 0 TOG)
   *   actual,      // box-score stat value (null when void)
   *   settledAt    // ISO 8601
   * }
   *
   * HONESTY RULES (enforced by grade(), below — a sharp subscriber will test all):
   *   1. DNP / scratch / 0 TOG  -> 'void', never 'loss'. Outs must not tank a record.
   *   2. Grade the CAPTURED line only. Never a line recomputed at settle time.
   *   3. Milestone (X+) uses >= ; two-way (X.5) uses strict >/< with push on exact.
   *   4. Units are flat 1u at the captured price. No compounding, no staking model.
   * ========================================================================== */

  // Registry of signal types. `priced:true` = carries a real posted line + price
  // and is graded for both hit-rate AND units. Unpriced/directional signals
  // (form direction, tag risk) are intentionally NOT registered for grading yet.
  var SIGNAL_DEFS = {
    green_light: { label: 'Green Lights', market: 'disposals', side: 'over',  lineType: 'twoway',    priced: true },
    death_rider: { label: 'Death Riders', market: 'disposals', side: 'under', lineType: 'twoway',    priced: true },
    snags:       { label: 'Snags',        market: 'goals',     side: 'over',  lineType: 'milestone', priced: true },
    streakers:   { label: 'Streakers',    market: 'dynamic',   side: 'over',  lineType: 'milestone', priced: true }
    // phase-2 generators register here as they move into this module.
  };

  function _round(x) { return x == null ? '' : ('R' + x); }
  function makeId(season, round, signalType, player, market, line, side) {
    return [season, _round(round), signalType, player, market, line, side].join('|');
  }

  /* ---- normalise a fired row -> the canonical frozen record ---- */
  // ctx = { season, round, capturedAt }
  function toSettleable(signalType, row, ctx) {
    var def = SIGNAL_DEFS[signalType];
    if (!def) return null;
    var player = (row.p && row.p.name) || row.player;
    if (!player) return null;
    var market = def.market === 'dynamic' ? (row.market || row.statKey) : def.market;
    var line = (row.line != null) ? row.line : (row.captureLine != null ? row.captureLine : null);
    if (line == null) return null;
    var side = row.side || def.side;
    return {
      id: makeId(ctx.season, ctx.round, signalType, player, market, line, side),
      signalType: signalType,
      season: String(ctx.season),
      round: ctx.round,
      capturedAt: ctx.capturedAt || new Date().toISOString(),
      player: player,
      team: row.team || (row.p && row.p.team) || null,
      opp: row.oppName || row.opp || null,
      market: market,
      line: line,
      lineType: row.lineType || def.lineType,
      side: side,
      price: (row.odds != null) ? row.odds : (row.price != null ? row.price : null),
      book: row.book || null,
      score: (row.score != null) ? row.score : null,
      closePrice: null,
      result: null,
      actual: null,
      settledAt: null
    };
  }

  /* ---- the grading engine (honesty rules 1-3 live here) ----
   * played: boolean — was the player in the box score with > 0 TOG this game?
   * actual: number  — the box-score value of record.market (ignored when !played) */
  function grade(rec, actual, played) {
    if (!played || actual == null || !isFinite(actual)) return { result: 'void', actual: null };
    var L = rec.line, over = rec.side === 'over', hit;
    if (rec.lineType === 'milestone') {                    // X+  -> >= , no push
      hit = actual >= L;
      return { result: over ? (hit ? 'win' : 'loss') : (hit ? 'loss' : 'win'), actual: actual };
    }
    if (actual === L) return { result: 'push', actual: actual };   // two-way exact -> push
    hit = actual > L;
    return { result: over ? (hit ? 'win' : 'loss') : (hit ? 'loss' : 'win'), actual: actual };
  }

  /* ---- flat 1u P/L at the captured price (rule 4) ---- */
  function pnl(rec) {
    if (rec.result === 'win')  return (rec.price != null ? rec.price - 1 : 0);
    if (rec.result === 'loss') return -1;
    return 0;                                              // push / void / ungraded
  }

  /* ---- rollup a settled ledger slice into a per-signal scorecard ---- */
  // rows: array of settled records. returns { <signalType>: {n,wins,losses,pushes,voids,hitRate,units,roi} }
  function rollup(rows) {
    var by = {};
    (rows || []).forEach(function (r) {
      var b = by[r.signalType] || (by[r.signalType] = { n: 0, wins: 0, losses: 0, pushes: 0, voids: 0, units: 0 });
      if (r.result === 'void' || r.result == null) { b.voids++; return; }      // voids excluded from n
      b.n++;
      if (r.result === 'win') b.wins++;
      else if (r.result === 'loss') b.losses++;
      else if (r.result === 'push') b.pushes++;
      b.units += pnl(r);
    });
    Object.keys(by).forEach(function (k) {
      var b = by[k], decided = b.wins + b.losses;
      b.hitRate = decided ? b.wins / decided : null;       // pushes don't count for/against hit-rate
      b.roi = b.n ? b.units / b.n : null;                  // units per bet placed
    });
    return by;
  }

  /* ============================================================================
   * GENERATORS (dependency-injected)
   * ----------------------------------------------------------------------------
   * create(deps) -> generator fns bound to the caller's runtime. Deps:
   *   JTTScoring        the scoring engine (window.JTTScoring / require('./scoring.js'))
   *   oddsFor(name,mkt) -> {line,over,under,book} | null   (best price, respects book filter)
   *   nextOpp(team)     -> opponent team name | null
   *   playersOnTeam(t)  -> [player, …]
   *   hasAnyOdds()      -> boolean
   *   SIGNAL_MIN_ODDS   -> { over:Number|null, under:Number|null }
   * ========================================================================== */
  function create(deps) {
    var JTTScoring     = deps.JTTScoring;
    var oddsFor        = deps.oddsFor;
    var nextOpp        = deps.nextOpp;
    var playersOnTeam  = deps.playersOnTeam;
    var hasAnyOdds     = deps.hasAnyOdds;
    var SIGNAL_MIN_ODDS = deps.SIGNAL_MIN_ODDS || { over: null, under: null };

    // ---- Green Lights (kind='over') / Death Riders (kind='under') ----
    // EXTRACTED VERBATIM from index.html so the browser and capture agree byte-for-byte.
    function collectOU(teamList, kind) {
      if (!JTTScoring || !hasAnyOdds()) return [];
      var side = kind === 'over' ? 'over' : 'under';
      var minOdds = SIGNAL_MIN_ODDS[side];
      var out = [];
      teamList.forEach(function (team) {
        var opp = nextOpp(team); if (!opp) return;
        playersOnTeam(team).forEach(function (p) {
          var od = oddsFor(p.name, 'disposals');                 // line must exist…
          if (!od || od.line == null) return;
          var price = od[side];
          if (price == null) return;                             // …and have a price this side
          if (minOdds != null && price < minOdds) return;        // …above the signal's floor
          var r = kind === 'over' ? JTTScoring.scoreOverLine(p, opp, od.line) : JTTScoring.scoreUnderLine(p, opp, od.line);
          if (r) { r.team = team; r.oppName = opp; r.odds = price; r.book = od.book; out.push(r); }
        });
      });
      return out.sort(function (a, b) { return kind === 'over' ? b.score - a.score : a.score - b.score; });
    }

    // Capture helper: fire OU signals for a team list and normalise to settleable records.
    function captureOU(teamList, ctx) {
      var recs = [];
      collectOU(teamList, 'over').forEach(function (r) {
        r.lineType = 'twoway'; var s = toSettleable('green_light', r, ctx); if (s) recs.push(s);
      });
      collectOU(teamList, 'under').forEach(function (r) {
        r.lineType = 'twoway'; var s = toSettleable('death_rider', r, ctx); if (s) recs.push(s);
      });
      return recs;
    }

    return { collectOU: collectOU, captureOU: captureOU };
  }

  return {
    create: create,
    SIGNAL_DEFS: SIGNAL_DEFS,
    makeId: makeId,
    toSettleable: toSettleable,
    grade: grade,
    pnl: pnl,
    rollup: rollup
  };
});
