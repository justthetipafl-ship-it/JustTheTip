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
    var teamLogo = deps.teamLogo || function () { return ''; };
    var _degBadges = deps._degBadges || function () { return ''; };
    var _fixtureSet = deps._fixtureSet || function () { return { has: function () { return true; } }; };
    var logsFor = deps.logsFor || function () { return []; };
    var players = deps.players || [];
    var JS = deps.JTTScoring || window.JTTScoring;
    var priceForLine = deps.priceForLine;
    var bookName = deps.bookName || function(b){return b||'';};
    var degRow = deps.degRow || function(){return '';};
    var CFG = (typeof window !== 'undefined' && window.SPORT_CONFIG) || {};
    var MKT = CFG.mktNames || {};
    function mktLabel(k) { return MKT[k] || k; }

    // markets a player's position can be graded on
    var OUTFIELD = ['shots', 'shotsOn', 'goals', 'assists', 'tackles', 'foulsCommitted', 'cards'];   // bettable markets only (no key passes)
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
        lineRow = '<div class="lc-line"><span class="lc-line-pick">O' + c.line + ' ' + esc(mktLabel(c.mkt)) + '</span>' + ((pr && pr.price != null) ? '<span class="lc-odds">$' + (+pr.price).toFixed(2) + (pr.book ? ' <span class="lc-book">' + esc(bookName(pr.book)) + '</span>' : '') + '</span>' : '<span class="lc-odds noodds">no line</span>') + '</div>'; }
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
          if (mkt === 'keyPasses' || mkt === 'assists') return;   // not surfaced in Falling Off
          if (!marketHasData(mkt)) return;
          var all = recentLogs(p.name); if (all.length < 8) return;
          var recent = l5avg(p.name, mkt), older = all.slice(5, 10);
          if (!older.length) return;
          var olderAvg = older.reduce(function (s, r) { return s + (r[mkt] || 0); }, 0) / older.length;
          if (olderAvg < 0.5 || recent >= olderAvg * 0.6) return;   // needs a meaningful prior baseline (>=0.5) so trivial ~0.2->0 drops don't surface, and recent form collapsed vs prior
          var drop = 1 - recent / olderAvg;
          out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: drop * 10, mkt: mkt, recent: recent, older: olderAvg, drop: drop });
        });
      });
      out.sort(function (a, b) { return b.sc - a.sc; });
      if (!out.length) return emptyState('ti-trending-down', 'Falling Off', 'Nobody in a notable downtrend on the slate.');
      var rows = out.slice(0, 12).map(function (c) {
        var sub = posShort(c.p.pos) + ' \u00b7 ' + abbr(c.p.team) + (c.opp ? ' v ' + abbr(c.opp) : '') + ' \u00b7 ' + mktLabel(c.mkt) + ' L5 ' + c.recent.toFixed(1) + ' vs ' + c.older.toFixed(1) + ' prior';
        return degRow(c.p.name, '#ef4444', '\u2193 ' + Math.round(c.drop * 100) + '%', sub, c.p.name);
      });
      return degWrap('ti-trending-down', 'Falling Off', rows, 'c-tough');
    }
    // Streakers — cleared their line in N straight games (AFL Streakers).
    function streakers() {
      var out = [];
      slatePlayers().forEach(function (p) {
        marketsFor(p).forEach(function (mkt) {
          if (!marketHasData(mkt)) return;
          var avg = l5avg(p.name, mkt), line = JS.drLine(avg, mkt); if (line == null) return;
          var lg = recentLogs(p.name), streak = 0;
          for (var i = 0; i < lg.length; i++) { if ((lg[i][mkt] || 0) >= line) streak++; else break; }
          if (streak < 3) return;
          out.push({ p: p, opp: JS.oppOfTeam(p.team), sc: streak + line / 10, line: line, mkt: mkt, l5: l5vals(p.name, mkt),
            headline: streak + '-game streak \u00b7 O' + line + ' ' + mktLabel(mkt),
            detail: 'cleared ' + mktLabel(mkt) + ' O' + line + ' in ' + streak + ' straight' });
        });
      });
      return wrap('ti-flame', 'Streakers', out, 'c-soft', 'No active streaks worth riding right now.');
    }
    // Form Alerts — AFL-style compact list: L3 vs last-10 baseline, both ways, grouped by team.
    function formAlerts() {
      var all = [];
      slatePlayers().forEach(function (p) {
        marketsFor(p).forEach(function (mkt) {
          if (!marketHasData(mkt)) return;
          var lg = recentLogs(p.name); if (lg.length < 6) return;                    // most-recent first
          var l3 = lg.slice(0, 3).reduce(function (t, r) { return t + (r[mkt] || 0); }, 0) / 3;
          var base = lg.slice(0, 10), baseAvg = base.reduce(function (t, r) { return t + (r[mkt] || 0); }, 0) / base.length;
          if (baseAvg <= 0.3) return;
          var swing = (l3 - baseAvg) / baseAvg;
          if (Math.abs(swing) < 0.35) return;
          var up = swing > 0;
          if (up && l3 < 0.5) return;
          if (!up && baseAvg < 0.5) return;
          all.push({ p: p, mkt: mkt, l3: l3, baseAvg: baseAvg, swing: swing, up: up });
        });
      });
      if (!all.length) return emptyState('ti-temperature-celsius', 'Form Alerts', 'No hot or cold form swings on the slate.');
      var byTeam = {}; all.forEach(function (a) { (byTeam[a.p.team] = byTeam[a.p.team] || []).push(a); });
      var order = Object.keys(byTeam).sort(function (x, y) { return byTeam[y].length - byTeam[x].length; });
      var pfRow = function (a) {
        var col = a.up ? '#22c55e' : '#ef4444', arr = a.up ? '\u25b2' : '\u25bc', sign = a.up ? '+' : '';
        return '<div class="pf-row" onclick="openPlayer(\'' + esc(a.p.name).replace(/\x27/g, "\\\x27") + '\')">' +
          '<span class="pf-sw" style="color:' + col + '">' + arr + ' ' + sign + Math.round(a.swing * 100) + '%</span>' +
          '<span class="pf-nm">' + esc(a.p.name) + '</span>' +
          '<span class="pf-info">' + mktLabel(a.mkt) + ' \u00b7 L3 ' + a.l3.toFixed(1) + ' vs ' + a.baseAvg.toFixed(1) + '</span></div>';
      };
      var items = order.map(function (t) {
        return '<div class="pf-team">' + teamLogo(t, 16) + '<span>' + esc(t) + '</span><span class="pf-ct">' + byTeam[t].length + '</span></div>' +
          byTeam[t].sort(function (x, y) { return Math.abs(y.swing) - Math.abs(x.swing); }).map(pfRow).join('');
      });
      return degWrap('ti-temperature-celsius', 'Form Alerts', items, 'c-green');
    }
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

    // Green Lights — the strongest overs: high hit rate + model agreement + soft matchup.
    function greenLights() {
      var out = [];
      slatePlayers().forEach(function (p) {
        marketsFor(p).forEach(function (mkt) {
          if (!marketHasData(mkt)) return;
          var avg = l5avg(p.name, mkt), line = JS.drLine(avg, mkt); if (line == null) return;
          var hr = JS.getHitRate(p.name, mkt, line, false); if (!hr || hr.n < 5 || hr.rate < 0.7) return;
          var pr = JS.prob(p, mkt, line); if (pr < 0.62) return;                     // model backs the over
          var opp = JS.oppOfTeam(p.team), dvp = opp ? JS.getDVPPct(opp, p.pos, mkt) : null;
          out.push({ p: p, opp: opp, sc: hr.rate * 10 + (pr - 0.5) * 4 + (dvp && dvp > 0 ? dvp / 12 : 0), line: line, mkt: mkt, l5: l5vals(p.name, mkt),
            headline: 'O' + line + ' ' + mktLabel(mkt),
            detail: Math.round(hr.rate * 100) + '% hit (' + hr.n + ') \u00b7 model ' + Math.round(pr * 100) + '%' + (dvp != null && dvp > 4 ? ' \u00b7 soft matchup' : '') });
        });
      });
      return wrap('ti-traffic-lights', 'Green Lights', out, 'c-green', 'No strong-enough overs to green-light this week.');
    }
    // Bunnies — feasts on this opponent historically (>=3 meetings, best matchup of any team faced).
    function bunnies() {
      var out = [];
      slatePlayers().forEach(function (p) {
        var opp = JS.oppOfTeam(p.team); if (!opp) return;
        var byOpp = {}; recentLogs(p.name).forEach(function (r) { var o = r.Opp || r.opp; if (o) (byOpp[o] = byOpp[o] || []).push(r); });
        var vt = byOpp[opp]; if (!vt || vt.length < 3) return;
        marketsFor(p).forEach(function (mkt) {
          if (!marketHasData(mkt)) return;
          var thisAvg = vt.reduce(function (t, r) { return t + (r[mkt] || 0); }, 0) / vt.length;
          if (thisAvg < 0.5) return;
          var best = null;
          Object.keys(byOpp).forEach(function (o) { if (o === opp || byOpp[o].length < 2) return; var a = byOpp[o].reduce(function (t, r) { return t + (r[mkt] || 0); }, 0) / byOpp[o].length; if (best == null || a > best) best = a; });
          if (best == null || thisAvg <= best) return;                               // genuinely their best matchup
          var diff = best > 0 ? (thisAvg - best) / best * 100 : 0;
          out.push({ p: p, opp: opp, sc: diff + thisAvg, line: null, mkt: mkt, l5: vt.slice(0, 5).map(function (r) { return r[mkt] || 0; }),
            headline: 'Feasts on ' + abbr(opp) + ' \u00b7 ' + mktLabel(mkt),
            detail: thisAvg.toFixed(1) + ' avg vs ' + abbr(opp) + ' (' + vt.length + ' mtgs) vs ' + best.toFixed(1) + ' elsewhere' });
        });
      });
      return wrap('ti-carrot', 'Bunnies', out, 'c-soft', 'No standout opponent history on the slate.');
    }
    var tiles = {
      'locked-in': lockedIn, 'green-lights': greenLights, 'streakers': streakers, 'bunnies': bunnies, 'form-alerts': formAlerts,
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
