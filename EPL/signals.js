/* JTT EPL — signals.js  (window.JTTSignals)
   The soccer Degen Crew, ported from the WC monolith into the shell's create(deps) shape.
   Each engine iterates the slate, reads gamelog rows through epl/scoring.js, and emits cards.
   Data-gated: shot/foul engines show a clear "lands with your key" state until API-Football data
   is present, then auto-activate. Generic finders (Locked In / Falling Off / On a Run / Form Alerts)
   run across every market that has data, so they cover goals/assists/tackles/cards/saves now and
   shots/fouls automatically later. */
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
    var logsFor = deps.logsFor || function () { return []; };
    var players = deps.players || [];
    var JS = deps.JTTScoring || window.JTTScoring;
    var priceForLine = deps.priceForLine;
    var CFG = (typeof window !== 'undefined' && window.SPORT_CONFIG) || {};
    var MKT = CFG.mktNames || {};
    function mktLabel(k) { return MKT[k] || k; }

    // markets a player's position can be graded on
    var OUTFIELD = ['shots', 'shotsOn', 'goals', 'assists', 'keyPasses', 'tackles', 'foulsCommitted', 'cards'];
    function marketsFor(p) { return p.pos === 'GK' ? ['saves'] : OUTFIELD.slice(); }

    function slatePlayers() {
      var set = _fixtureSet();
      return (players || []).filter(function (p) {
        if (!set.has(p.team)) return false;
        if (p.status && p.status !== 'a') return false;   // injured/suspended/doubtful
        return (logsFor(p.name) || []).length >= 3;
      });
    }
    function recentLogs(name) {
      var lg = (logsFor(name) || []).slice();
      lg.sort(function (a, b) { var da = a.date || '', db = b.date || ''; return db < da ? -1 : db > da ? 1 : 0; });
      return lg;   // most-recent first
    }
    function l5avg(name, mkt) { var lg = recentLogs(name).slice(0, 5); return lg.length ? lg.reduce(function (s, r) { return s + (r[mkt] || 0); }, 0) / lg.length : 0; }
    function l5hits(name, mkt, line) { var lg = recentLogs(name).slice(0, 5); return lg.filter(function (r) { return (r[mkt] || 0) >= line; }).length; }
    function l5vals(name, mkt) { return recentLogs(name).slice(0, 5).map(function (r) { return r[mkt] || 0; }); }
    // is a market actually populated in the data yet? (shots null until the API-Football key lands)
    function marketHasData(mkt) {
      var pl = slatePlayers();
      for (var i = 0; i < pl.length && i < 40; i++) {
        var lg = logsFor(pl[i].name) || [];
        for (var j = 0; j < lg.length; j++) if (lg[j][mkt] != null) return true;
      }
      return false;
    }
    var hasDVP = function () { return (deps.dvp || []).length > 0; };

    function card(c) {
      var q = esc(c.p.name).replace(/'/g, "\\'");
      var spark = c.l5 ? '<div class="lc-l5"><span class="lc-l5-lbl">LAST 5</span><span class="lc-spark">' + c.l5.map(function (v) {
        var d = Number.isInteger(v) ? v : (+v).toFixed(1);
        return '<span class="v ' + (c.line != null && v >= c.line ? 'hit' : 'miss') + '">' + d + '</span>';
      }).join('') + '</span></div>' : '';
      var oddsMkt = (c.mkt === 'foulsCommitted' || c.mkt === 'foulsDrawn') ? 'fouls' : c.mkt;
      var lineRow = '';
      if (c.line != null && c.mkt) { var pr = (typeof priceForLine === 'function') ? priceForLine(c.p.name, oddsMkt, c.line) : null;
        lineRow = '<div class="lc-line"><span class="lc-line-pick">O' + c.line + ' ' + esc(mktLabel(c.mkt)) + '</span>' + (pr ? '<span class="lc-odds">$' + (+pr).toFixed(2) + '</span>' : '<span class="lc-odds noodds">no line</span>') + '</div>'; }
      return '<div class="lc-card" onclick="openPlayer(\'' + q + '\')">' +
        '<div class="lc-hd"><span class="lc-nm">' + esc(c.p.name) + '</span>' + _degBadges(c.p.name) +
        '<span class="lc-meta">' + posShort(c.p.pos) + ' \u00b7 ' + abbr(c.p.team) + (c.opp ? ' v ' + abbr(c.opp) : '') + '</span></div>' +
        lineRow + spark +
        '<div class="tp-body-meta" style="border:0;padding:2px 0 6px"><b>' + esc(c.headline) + '</b> \u00b7 ' + esc(c.detail) + '</div></div>';
    }
    function wrap(icon, title, out, cls, emptyMsg) {
      out.sort(function (a, b) { return b.sc - a.sc; });
      if (!out.length) return emptyState(icon, title, emptyMsg);
      return degWrap(icon, title, out.slice(0, 12).map(card), cls);
    }
    var KEY_PENDING = 'Shots & fouls light up here the moment your API-Football key is in.';

    // best OVER market for a player (highest L5 hits, tie-broken by model edge)
    function bestOver(p, minHits) {
      var best = null;
      marketsFor(p).forEach(function (mkt) {
        if (!marketHasData(mkt)) return;
        var avg = l5avg(p.name, mkt), line = JS.drLine(avg, mkt); if (line == null) return;
        var lg = recentLogs(p.name).slice(0, 5); if (lg.length < 4) return;
        var hits = l5hits(p.name, mkt, line); if (hits < (minHits || 4)) return;
        var sc = hits + (JS.prob(p, mkt, line) - 0.5) * 2;
        if (!best || sc > best.sc) best = { mkt: mkt, label: mktLabel(mkt), line: line, hits: hits, n: lg.length, sc: sc };
      });
      return best;
    }

    // ---------- generic finders (run across every populated market) ----------
    function lockedIn() {
      var out = [];
      slatePlayers().forEach(function (p) {
        var b = bestOver(p, 4); if (!b) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: b.sc, line: b.line, mkt: b.mkt, l5: l5vals(p.name, b.mkt),
          headline: b.hits + '/' + b.n + ' \u00b7 O' + b.line + ' ' + b.label,
          detail: 'hitting ' + b.label + ' O' + b.line + ' in ' + b.hits + ' of last ' + b.n });
      });
      return wrap('ti-lock-square-rounded', 'Locked In', out, 'c-soft', 'No players locked into a line right now.');
    }
    function fallingOff() {
      var out = [];
      slatePlayers().forEach(function (p) {
        marketsFor(p).forEach(function (mkt) {
          if (!marketHasData(mkt)) return;
          var all = recentLogs(p.name); if (all.length < 8) return;
          var recent = l5avg(p.name, mkt), older = all.slice(5, 10);
          if (!older.length) return;
          var olderAvg = older.reduce(function (s, r) { return s + (r[mkt] || 0); }, 0) / older.length;
          if (olderAvg <= 0 || recent >= olderAvg * 0.6) return;   // recent form collapsed vs prior
          var drop = 1 - recent / olderAvg;
          out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: drop * 10, mkt: mkt, l5: l5vals(p.name, mkt), line: olderAvg,
            headline: mktLabel(mkt) + ' trending under',
            detail: 'L5 ' + recent.toFixed(1) + ' vs prior ' + olderAvg.toFixed(1) + ' (' + Math.round(drop * 100) + '% down)' });
        });
      });
      return wrap('ti-trending-down', 'Falling Off', out, 'c-tough', 'Nobody in a notable downtrend on the slate.');
    }
    function onARun() {
      var out = [];
      slatePlayers().forEach(function (p) {
        marketsFor(p).forEach(function (mkt) {
          if (!marketHasData(mkt)) return;
          var avg = l5avg(p.name, mkt), line = JS.drLine(avg, mkt); if (line == null) return;
          var lg = recentLogs(p.name), streak = 0;
          for (var i = 0; i < lg.length; i++) { if ((lg[i][mkt] || 0) >= line) streak++; else break; }
          if (streak < 3) return;
          out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: streak + line / 10, line: line, mkt: mkt, l5: l5vals(p.name, mkt),
            headline: streak + '-game run \u00b7 O' + line + ' ' + mktLabel(mkt),
            detail: 'cleared ' + mktLabel(mkt) + ' O' + line + ' in ' + streak + ' straight' });
        });
      });
      return wrap('ti-run', 'On a Run', out, 'c-soft', 'No active streaks worth riding right now.');
    }
    function formAlerts() {
      var out = [];
      slatePlayers().forEach(function (p) {
        marketsFor(p).forEach(function (mkt) {
          if (!marketHasData(mkt)) return;
          var avg = l5avg(p.name, mkt), line = JS.drLine(avg, mkt); if (line == null) return;
          var sf = JS.seasonForm(p.name, mkt, line); if (!sf || sf.games < 3) return;
          if (sf.rate >= 0.8) out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: sf.rate * 10, line: line, mkt: mkt, l5: l5vals(p.name, mkt),
            headline: 'Hot \u00b7 ' + mktLabel(mkt), detail: sf.hits + '/' + sf.games + ' this season at O' + line });
          else if (sf.rate <= 0.2) out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: (1 - sf.rate) * 8, line: line, mkt: mkt, l5: l5vals(p.name, mkt),
            headline: 'Cold \u00b7 ' + mktLabel(mkt), detail: 'only ' + sf.hits + '/' + sf.games + ' this season at O' + line });
        });
      });
      return wrap('ti-temperature-celsius', 'Form Alerts', out, 'c-neu', 'No hot or cold streaks flagged this week.');
    }

    // ---------- FPL-ready stat engines ----------
    function tackleMachines() {
      var out = [];
      slatePlayers().forEach(function (p) {
        if (p.pos === 'GK' || p.pos === 'FWD') return;
        var avg = l5avg(p.name, 'tackles'), line = JS.drLine(avg, 'tackles'); if (line == null || line < 1.5) return;
        var hr = JS.getHitRate(p.name, 'tackles', line, false); if (!hr || hr.rate < 0.55) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: hr.rate * 10 + line, line: line, mkt: 'tackles', l5: l5vals(p.name, 'tackles'),
          headline: 'O' + line + ' Tackles', detail: 'L5 ' + avg.toFixed(1) + ' \u00b7 hit ' + Math.round(hr.rate * 100) + '% (' + hr.n + ')' });
      });
      return wrap('ti-shield-half', 'Tackle Machines', out, 'c-soft', 'No standout tackle volume on the slate.');
    }
    function brickWall() {
      var out = [];
      slatePlayers().forEach(function (p) {
        if (p.pos !== 'GK') return;
        var avg = l5avg(p.name, 'saves'), line = JS.drLine(avg, 'saves'); if (line == null || line < 1.5) return;
        var hr = JS.getHitRate(p.name, 'saves', line, false); if (!hr || hr.rate < 0.5) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: hr.rate * 10 + line, line: line, mkt: 'saves', l5: l5vals(p.name, 'saves'),
          headline: 'O' + line + ' Saves', detail: 'L5 ' + avg.toFixed(1) + ' \u00b7 hit ' + Math.round(hr.rate * 100) + '% (' + hr.n + ')' });
      });
      return wrap('ti-shield', 'Brick Wall', out, 'c-soft', 'No goalkeeper save spots stand out.');
    }
    function spamSquare() {
      var out = [];
      slatePlayers().forEach(function (p) {
        var lg = recentLogs(p.name); if (lg.length < 5) return;
        var cards = lg.filter(function (r) { return (r.cards || r.yellowCard || 0) >= 1; }).length, rate = cards / lg.length;
        if (rate < 0.4) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: rate * 10, headline: 'Card magnet',
          detail: 'booked in ' + cards + ' of last ' + lg.length + ' (' + Math.round(rate * 100) + '%)' });
      });
      return wrap('ti-square-letter-x', 'Spam Square', out, 'c-tough', 'No obvious card magnets on the slate.');
    }
    function penaltyKings() {
      var out = [];
      slatePlayers().forEach(function (p) {
        if (p.pens_order !== 1) return;
        var gAvg = l5avg(p.name, 'goals');
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: 5 + gAvg * 3, headline: 'On penalties',
          detail: 'first-choice taker' + (gAvg > 0 ? ' \u00b7 L5 goals ' + gAvg.toFixed(1) : '') });
      });
      return wrap('ti-crown', 'Penalty Kings', out, 'c-soft', 'No confirmed penalty takers on the slate.');
    }
    function goalsGalore() {
      var out = [];
      slatePlayers().forEach(function (p) {
        if (p.pos === 'GK') return;
        var line = 0.5, hr = JS.getHitRate(p.name, 'goals', line, false); if (!hr || hr.n < 4) return;
        if (hr.rate >= 0.45) out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: hr.rate * 10, line: line, mkt: 'goals', l5: l5vals(p.name, 'goals'),
          headline: 'Anytime scorer', detail: 'scored in ' + Math.round(hr.rate * 100) + '% (' + hr.n + ') \u00b7 xG ' + (p.xG != null ? (+p.xG).toFixed(2) : 'n/a') });
      });
      return wrap('ti-arrows-up-down', 'Goals Galore', out, 'c-soft', 'No standout anytime-scorer spots.');
    }
    function firstGoal() {
      var out = [];
      slatePlayers().forEach(function (p) {
        if (p.pos === 'GK') return;
        var gr = JS.getHitRate(p.name, 'goals', 0.5, false); if (!gr || gr.n < 4 || gr.rate < 0.35) return;
        var setPiece = p.pens_order === 1 || p.direct_fk_order === 1;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: gr.rate * 10 + (setPiece ? 1 : 0),
          headline: 'Opener candidate', detail: 'scores ' + Math.round(gr.rate * 100) + '% \u00b7 xG ' + (p.xG != null ? (+p.xG).toFixed(2) : 'n/a') + (setPiece ? ' \u00b7 set pieces' : '') });
      });
      return wrap('ti-target-arrow', 'First Goal', out, 'c-soft', 'No strong opener candidates on the slate.');
    }

    // ---------- data-gated engines (activate when the key/data lands) ----------
    function tapIns() {
      if (!marketHasData('shots')) return emptyState('ti-hand-finger', 'Tap-Ins', KEY_PENDING);
      var out = [];
      slatePlayers().forEach(function (p) {
        if (p.pos === 'GK') return;
        var sh = l5avg(p.name, 'shots'), sot = l5avg(p.name, 'shotsOn'); if (sh < 1) return;
        var conv = sot / sh;
        if (conv < 0.5) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: conv * 10 + sot, mkt: 'shotsOn', l5: l5vals(p.name, 'shotsOn'), line: 0.5,
          headline: 'High conversion', detail: 'L5 ' + sh.toFixed(1) + ' shots \u00b7 ' + Math.round(conv * 100) + '% on target' });
      });
      return wrap('ti-hand-finger', 'Tap-Ins', out, 'c-soft', 'No high-conversion shooters on the slate.');
    }
    function fouledAgain() {
      if (!marketHasData('foulsCommitted') && !marketHasData('foulsDrawn')) return emptyState('ti-hand-stop', 'Fouled Again', KEY_PENDING);
      var out = [], mkt = marketHasData('foulsDrawn') ? 'foulsDrawn' : 'foulsCommitted';
      slatePlayers().forEach(function (p) {
        var avg = l5avg(p.name, mkt), line = JS.drLine(avg, 'foulsCommitted'); if (line == null || line < 0.5) return;
        var hr = JS.getHitRate(p.name, mkt, line, false); if (!hr || hr.rate < 0.55) return;
        out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: hr.rate * 10 + line, line: line, mkt: mkt, l5: l5vals(p.name, mkt),
          headline: 'O' + line + ' ' + mktLabel(mkt), detail: 'L5 ' + avg.toFixed(1) + ' \u00b7 hit ' + Math.round(hr.rate * 100) + '%' });
      });
      return wrap('ti-hand-stop', 'Fouled Again', out, 'c-soft', 'No foul magnets on the slate.');
    }
    function cornerStorm() {
      return emptyState('ti-flag-3', 'Corner Storm', 'Match corner totals land with the team-stats build.');
    }
    function mismatch() {
      if (!hasDVP()) return emptyState('ti-bolt', 'Mismatch Alert', 'Opponent difficulty (DVP) lands with the data build.');
      var out = [];
      slatePlayers().forEach(function (p) {
        if (p.pos === 'GK') return;
        var opp = JS.oppOfTeam(p.team); if (!opp) return;
        var mkt = marketHasData('shots') ? 'shots' : 'goals';
        var pct = JS.getDVPPct(opp, p.pos, mkt); if (pct == null || pct < 12) return;
        var avg = l5avg(p.name, mkt); if (avg <= 0) return;
        out.push({ p: p, opp: opp, sc: pct + avg, headline: 'Attacker vs leaky D',
          detail: abbr(opp) + ' concede +' + Math.round(pct) + '% ' + mktLabel(mkt) + ' \u00b7 L5 ' + avg.toFixed(1) });
      });
      return wrap('ti-bolt', 'Mismatch Alert', out, 'c-soft', 'No standout attacker-vs-defence mismatches.');
    }

    var tiles = {
      'locked-in': lockedIn, 'falling-off': fallingOff, 'on-a-run': onARun, 'form-alerts': formAlerts,
      'first-goal': firstGoal, 'tap-ins': tapIns, 'penalty-kings': penaltyKings, 'spam-square': spamSquare,
      'goals-galore': goalsGalore, 'corner-storm': cornerStorm, 'mismatch': mismatch, 'brick-wall': brickWall,
      'tackle-machines': tackleMachines, 'fouled-again': fouledAgain
    };

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
