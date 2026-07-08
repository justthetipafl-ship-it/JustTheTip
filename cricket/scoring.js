/* ============================================================
   scoring.js — JTT Cricket scoring engine (build 1)
   ============================================================
   Cricket implementation of the window.JTTScoring interface used by
   the AFL tool, so AFL-standard features (Degen tiles, Check My Bet,
   Multi Builder, Signal Ledger) port onto familiar function names.

   Wire-up:
     JTTScoring.configure({ rows, teams, ratings, scope:{format,level} });
   then call:
     JTTScoring.players() / playerByName(name)
     JTTScoring.scoreCMP(player, statKey, line, oppTeam)
     JTTScoring.scoreOverLine / scoreUnderLine   (anchor market: runs)
     JTTScoring.verdict / cmpFactors / getContextSignals
     JTTScoring.getHitRate / getL5Avg / getRecentAvg
     JTTScoring.getDVPPct(opp, roleGroup, statKey)   — attack-vs-role
     JTTScoring.venuePct(venue, statKey)             — venue factor

   Cricket-specific translations of the AFL model:
     - "current season" -> rolling 12-month form window (isCurWindow).
     - positions -> batting roles from row-level batOrder
       (OP 1-2, TOP 3-4, MID 5-7, LOW 8+) + bowler phase roles.
     - DVP -> attack-vs-role: what the opposition concedes to each
       batting role, per format, vs league average. For bowler wickets
       markets the matchup is the opposition's wickets-lost-per-match.
     - venue -> first-class factor (runs / boundary / wicket rates per
       format vs format average), fuzzy-keyed to survive naming drift
       between Cricsheet and CricAPI.
     - The TOG>=50 representative-game gate has no cricket analogue:
       a 2-ball duck is a real innings. All innings count.

   Pipeline TODOs noted inline: dismissedBy (true Bunnies) and
   ballsPP/ballsDeath (true bowler phase usage) need log fields.
   ============================================================ */
window.JTTScoring = (function () {
  "use strict";

  // ---- module data (set by configure) ----
  let ROWS = [], TEAMS = {}, RATINGS = {};
  let SCOPE = { format: "T20", level: null };   // level null = INTL+LEAGUE
  let _byName = {}, _players = [], _playerIdx = {};
  let _teamProf = {}, _teamAvgs = {};
  let _attack = {}, _attackAvgs = {};
  let _venues = {}, _venueAvgs = {};
  let _winCutoff = "";
  const _ctxSigCache = {}, _l5Cache = {};

  // ---- constants ----
  // Role groups (row-level, from batOrder at the time of the innings)
  const ROLE_TO_DVP = { OP: "OP", TOP: "TOP", MID: "MID", LOW: "LOW", BOWL: "BOWL" };
  const POS_TO_DVP = ROLE_TO_DVP;                 // AFL-name alias for ports
  const ROLE_LABEL = { OP: "Opener", TOP: "Top order", MID: "Middle order", LOW: "Lower order", BOWL: "Bowler" };
  const BAT_KEYS = new Set(["runs", "fours", "sixes", "balls", "sr"]);
  const BOWL_KEYS = new Set(["wickets", "dots", "maidens", "runsConceded", "econ"]);

  // stat -> opposition team-profile key used for the headline matchup %.
  // sign: +1 means "higher opp value is GOOD for the over" on this market.
  const MU_MAP = {
    runs:        { k: "bowlEcon",   sign: +1 },   // leaky attack -> runs
    fours:       { k: "foursConcPM", sign: +1 },
    sixes:       { k: "sixesConcPM", sign: +1 },
    balls:       { k: "wktsPM",     sign: -1 },   // wicket-taking attack -> fewer balls faced
    wickets:     { k: "wktsLostPM", sign: +1 },   // brittle batting -> bowler wickets
    dots:        { k: "batSR",      sign: -1 },   // slow scorers -> dots
    maidens:     { k: "batSR",      sign: -1 },
    runsConceded:{ k: "batSR",      sign: +1 },   // fast scorers -> concessions (an UNDER market)
  };

  // Empirical CV bands (measured on 24mo of scoped logs, 10+ innings samples).
  // Cricket variance is ~2x AFL's: runs/wkts p30≈0.85 p70≈1.05; count markets wilder.
  function cvBands(statKey) {
    if (statKey === "sixes") return { lo: 1.25, hi: 1.65 };
    if (statKey === "fours") return { lo: 1.00, hi: 1.25 };
    return { lo: 0.85, hi: 1.05 };   // runs, wickets, dots, balls, runsConceded
  }

  function roleOfOrder(o) {
    if (o == null) return "MID";
    if (o <= 2) return "OP";
    if (o <= 4) return "TOP";
    if (o <= 7) return "MID";
    return "LOW";
  }

  function venueKey(v) {
    if (!v) return null;
    let s = String(v).toLowerCase().split(",")[0];
    s = s.replace(/[^a-z0-9 ]/g, "").replace(/\b(the|stadium|ground|oval|international|cricket)\b/g, "").replace(/\s+/g, " ").trim();
    return s || null;
  }

  function inScope(r) {
    if (SCOPE.format && r.format !== SCOPE.format) return false;
    if (SCOPE.level && r.level !== SCOPE.level) return false;
    return true;
  }
  function isCurWindow(r) { return r.date >= _winCutoff; }

  // ---- accessors ----
  function rowsOf(name) { return (_byName[name] || []).filter(inScope); }
  function batRows(name) { return rowsOf(name).filter(r => r.bat); }
  function bowlRows(name) { return rowsOf(name).filter(r => r.bowl); }
  function rowsFor(name, statKey) { return BOWL_KEYS.has(statKey) ? bowlRows(name) : batRows(name); }
  function statOf(r, k) { const v = r[k]; return v == null ? 0 : +v; }

  // ---- matchup colour bucket (identical semantics to AFL) ----
  function muInfo(pct) {
    if (pct === null || pct === undefined) return null;
    if (pct > 15)  return { t: "Soft",    c: "#22c55e", cl: "c-soft" };
    if (pct > 5)   return { t: "Fav",     c: "#86efac", cl: "c-fav" };
    if (pct > -5)  return { t: "Neutral", c: "#9ca3af", cl: "c-neu" };
    if (pct > -15) return { t: "Tough",   c: "#f97316", cl: "c-tough" };
    return { t: "V.Tough", c: "#ef4444", cl: "c-vtough" };
  }

  // ---- attack-vs-role (the cricket DVP) ----
  // _attack[opp][role][statKey] = per-innings average that opp concedes to
  // that batting role in the current scope. _attackAvgs[role][statKey] = league avg.
  function getDVP(oppTeam, roleGroup, statKey) {
    const a = ((_attack[oppTeam] || {})[roleGroup] || {})[statKey];
    return a == null ? null : a;
  }
  function getDVPAvg(roleGroup, statKey) {
    const a = (_attackAvgs[roleGroup] || {})[statKey];
    return a == null ? null : a;
  }
  function getDVPPct(oppTeam, roleGroup, statKey) {
    // Bowler markets: matchup is the opposition's batting fragility/tempo.
    if (roleGroup === "BOWL") {
      const mu = MU_MAP[statKey];
      return mu ? muPct(oppTeam, mu.k, mu.sign) : null;
    }
    const val = getDVP(oppTeam, roleGroup, statKey);
    const avg = getDVPAvg(roleGroup, statKey);
    if (val === null || avg === null || avg === 0) return null;
    return ((val - avg) / avg) * 100;
  }
  function getDVPRank(oppTeam, roleGroup, statKey) {
    const entries = Object.keys(_attack)
      .map(t => ({ t, v: getDVP(t, roleGroup, statKey) }))
      .filter(e => e.v != null)
      .sort((a, b) => b.v - a.v);           // most conceded first = softest #1
    const i = entries.findIndex(e => e.t === oppTeam);
    return i < 0 ? null : { rank: i + 1, total: entries.length };
  }

  // ---- team profile percentages ----
  // signed: +% always means "good for the over" when sign passed via MU_MAP.
  function muPct(opp, profKey, sign) {
    const tp = _teamProf[opp];
    if (!tp || tp[profKey] == null) return null;
    const avg = _teamAvgs[profKey];
    if (!avg) return null;
    const raw = ((tp[profKey] - avg) / avg) * 100;
    return (sign == null ? 1 : sign) * raw;
  }
  function muPctFor(opp, statKey) {
    const mu = MU_MAP[statKey];
    return mu ? muPct(opp, mu.k, mu.sign) : null;
  }

  // ---- venue factor ----
  function venuePct(venue, statKey) {
    const vk = venueKey(venue);
    if (!vk) return null;
    const v = (_venues[vk] || {})[statKey];
    const avg = _venueAvgs[statKey];
    if (v == null || !avg) return null;
    return ((v - avg) / avg) * 100;
  }

  // ---- recent-form helpers (chronological; rows are date-sorted) ----
  function getL5Avg(name, statKey) {
    const key = name + "|" + statKey + "|" + SCOPE.format + "|" + (SCOPE.level || "");
    if (key in _l5Cache) return _l5Cache[key];
    const games = rowsFor(name, statKey);
    let v = null;
    if (games.length >= 2) {
      const r = games.slice(-5).map(g => statOf(g, statKey));
      v = r.reduce((a, b) => a + b, 0) / r.length;
    }
    _l5Cache[key] = v; return v;
  }
  function getRecentAvg(name, statKey, n) {
    if (n === 0) return null;
    const games = rowsFor(name, statKey); if (!games.length) return null;
    const r = games.slice(-n).map(g => statOf(g, statKey)); if (!r.length) return null;
    return r.reduce((a, b) => a + b, 0) / r.length;
  }
  function getHitRate(name, statKey, line, curOnly) {
    let games = rowsFor(name, statKey);
    if (curOnly) games = games.filter(isCurWindow);
    if (games.length < 3) return null;
    const hits = games.filter(g => statOf(g, statKey) >= line).length;
    return { rate: hits / games.length, n: games.length };
  }

  /* ============== CONTEXT SIGNALS ============== */
  // Cricket signal matrix. Each signal: {label, pct, weight}.
  // weight sign follows AFL semantics: pct*weight > 0 supports the OVER.
  function getContextSignals(p, statType, opp) {
    const cacheKey = (p.name || "") + "|" + (statType || "") + "|" + (opp || "") + "|" + SCOPE.format;
    if (_ctxSigCache[cacheKey]) return _ctxSigCache[cacheKey];
    const F = [];
    const tp = _teamProf[opp];
    const push = (label, profKey, weight) => {
      if (!tp || tp[profKey] == null || !_teamAvgs[profKey]) return;
      const pct = ((tp[profKey] - _teamAvgs[profKey]) / _teamAvgs[profKey]) * 100;
      F.push({ label, pct, weight });
    };
    const isBowl = BOWL_KEYS.has(statType);
    if (!isBowl) {
      push("Attack economy", "bowlEcon", 1.0);
      push("Wickets taken /match", "wktsPM", -0.8);
      push("Fours conceded /match", "foursConcPM", statType === "fours" ? 1.2 : 0.5);
      push("Sixes conceded /match", "sixesConcPM", statType === "sixes" ? 1.2 : 0.5);
      if (SCOPE.format === "T20" || SCOPE.format === "T100") {
        const role = p.batRole || "MID";
        if (role === "OP" || role === "TOP") push("Powerplay econ conceded", "ppEconConc", 1.0);
        if (role === "MID" || role === "LOW") push("Death econ conceded", "deathEconConc", 1.0);
      }
    } else {
      push("Batting strike rate", "batSR", statType === "dots" || statType === "maidens" ? -1.0 : 0.6);
      push("Wickets lost /match", "wktsLostPM", statType === "wickets" ? 1.2 : 0.6);
      if (SCOPE.format === "T20" || SCOPE.format === "T100") {
        const brole = p.bowlRole || "GEN";
        if (brole === "NEW") push("Opp powerplay run rate", "ppSR", -0.6);
        if (brole === "DEATH") push("Opp death run rate", "deathSR", 0.6); // hitters at death give chances
      }
    }
    _ctxSigCache[cacheKey] = F;
    return F;
  }

  // ---- default line from average (AFL drLine analogue, runs market) ----
  function drLine(avg) { if (avg < 10) return null; return Math.max(4.5, Math.round(avg * 0.76) - 0.5); }

  /* ============== CORE COMPOSITE SCORE ============== */
  // Mirrors AFL scoreCMP weighting shape exactly; cricket inputs.
  function scoreCMP(p, statKey, line, opp, venue) {
    const allGames = rowsFor(p.name, statKey);
    const curGames = allGames.filter(isCurWindow);
    const logGames = allGames;
    const curVals = curGames.map(r => statOf(r, statKey));
    const avg = curVals.length ? curVals.reduce((a, b) => a + b, 0) / curVals.length
                               : (p[statKey] || 0);
    const l5 = getL5Avg(p.name, statKey);
    const _l3g = allGames.slice(-3);
    const l3 = _l3g.length >= 3 ? _l3g.reduce((s, r) => s + statOf(r, statKey), 0) / _l3g.length : null;
    const windowHigh = curGames.reduce((mx, r) => Math.max(mx, statOf(r, statKey)), 0);
    const hits = logGames.filter(r => statOf(r, statKey) >= line).length;
    const hitRateRaw = logGames.length ? hits / logGames.length : null;
    const hitRateCurRaw = curGames.length
      ? curGames.filter(r => statOf(r, statKey) >= line).length / curGames.length : null;
    const _blendN = curGames.length;
    const _blendW = _blendN / (_blendN + 8);
    const hitRate = hitRateCurRaw !== null && hitRateRaw !== null
      ? _blendW * hitRateCurRaw + (1 - _blendW) * hitRateRaw : hitRateRaw;
    let cv = null;
    if (logGames.length >= 5) {
      const vals = logGames.map(r => statOf(r, statKey));
      const mn = vals.reduce((a, b) => a + b, 0) / vals.length;
      if (mn > 0) cv = Math.sqrt(vals.reduce((s, v) => s + Math.pow(v - mn, 2), 0) / vals.length) / mn;
    }
    const avgGap = avg - line;
    const l5Gap = l5 !== null ? l5 - line : null;
    const trend = l5 !== null && avg > 0 ? ((l5 - avg) / avg) * 100 : null;
    const roleGroup = BOWL_KEYS.has(statKey) ? "BOWL" : (p.batRole || "MID");
    const dvpPct = opp ? getDVPPct(opp, roleGroup, statKey) : null;
    const muPctVal = opp ? muPctFor(opp, statKey) : null;
    const muI = muInfo(muPctVal);
    const vsOppGames = opp ? allGames.filter(r => r.opp === opp) : [];
    const lastVsOpp = vsOppGames.length ? vsOppGames[vsOppGames.length - 1] : null;
    const lastVsOppVal = lastVsOpp ? statOf(lastVsOpp, statKey) : null;
    const venPct = venue ? venuePct(venue, statKey === "wickets" ? "wktsPI" :
                                     statKey === "sixes" ? "sixesPI" :
                                     statKey === "fours" ? "foursPI" : "runsPI") : null;
    const allSignals = opp ? getContextSignals(p, statKey, opp) : [];

    // relative thresholds: raw AFL cutoffs assume disposal-sized numbers, so
    // gap thresholds scale with the line (line 0.5 sixes vs line 27.5 runs).
    const gapBig = Math.max(1, line * 0.2), gapSmall = Math.max(0.3, line * 0.05);

    let score = 0;
    if (hitRate !== null) {
      if (hitRate >= 0.65) score += 3;
      else if (hitRate >= 0.5) score += 1.5;
      else if (hitRate < 0.35) score -= 2.5;
    }
    if (avgGap > gapBig) score += 1;
    else if (avgGap > gapSmall) score += 0.3;
    else if (avgGap <= -gapSmall) score -= 2;
    if (l3 !== null && avg > 0) {
      const l3Pct = ((l3 - avg) / avg) * 100;
      if (l3Pct > 15) score += 1.5;
      else if (l3Pct > 5) score += 0.8;
      else if (l3Pct < -15) score -= 1.5;
      else if (l3Pct < -5) score -= 0.8;
    }
    if (l5 !== null && logGames.length >= 5) {
      if (l5Gap !== null && l5Gap > 0 && trend !== null && trend > 5) score += 1.5;
      else if (l5Gap !== null && l5Gap < 0 && trend !== null && trend < -10) score -= 1.5;
    }
    if (hitRateCurRaw !== null && curGames.length >= 5 && Math.abs(hitRateCurRaw - (hitRate || 0)) > 0.1) {
      if (hitRateCurRaw > (hitRate || 0)) score += 0.5; else score -= 0.5;
    }
    const _cvb = cvBands(statKey);
    if (cv !== null) {
      if (cv < _cvb.lo) score += 0.8;
      else if (cv >= _cvb.hi) score -= 0.3;
    }
    if (dvpPct !== null) {
      if (dvpPct > 15) score += 3;
      else if (dvpPct > 5) score += 1.5;
      else if (dvpPct < -15) score -= 3;
      else if (dvpPct < -5) score -= 1.5;
    }
    if (muI && opp) {
      if (muI.t === "Soft" || muI.t === "Fav") score += 0.5;
      else if (muI.t === "Tough" || muI.t === "V.Tough") score -= 0.5;
    }
    if (lastVsOpp && opp && lastVsOppVal !== null) {
      if (lastVsOppVal >= line) score += 0.8;
      else if (lastVsOppVal < line * 0.8) score -= 0.5;
    }
    if (venPct !== null) {                           // cricket-only axis
      if (venPct > 12) score += 1.2;
      else if (venPct > 5) score += 0.6;
      else if (venPct < -12) score -= 1.2;
      else if (venPct < -5) score -= 0.6;
    }
    if (opp && allSignals.length) {
      allSignals.filter(s => Math.abs(s.pct) >= 5)
        .sort((a, b) => Math.abs(b.pct * b.weight) - Math.abs(a.pct * a.weight))
        .slice(0, 5).forEach(s => {
          const c = s.pct * s.weight;
          score += c > 0 ? Math.min(c * 0.06, 2) : Math.max(c * 0.06, -2);
        });
    }
    if (windowHigh > 0 && windowHigh < line && curGames.length >= 5) score -= 4;
    return score;
  }

  // ---- over / under wrappers — anchor market: RUNS (AFL used disposals) ----
  function scoreUnderLine(p, opp, line, venue) {
    if (!line || (p.runs || 0) < 8) return null;
    const curGames = batRows(p.name).filter(isCurWindow);
    if (curGames.length < 3) return null;
    const dvpPct = opp ? getDVPPct(opp, p.batRole || "MID", "runs") : null;
    if (dvpPct !== null && dvpPct >= 0) return null;
    const score = scoreCMP(p, "runs", line, opp, venue);
    const avg = p.runs || 0;
    const vals = curGames.map(r => r.runs || 0);
    const hits = vals.filter(v => v >= line).length;
    const hitRate = hits / vals.length;
    const l5 = getL5Avg(p.name, "runs");
    let verdictLabel, verdictCol;
    if (score <= -3.5) { verdictLabel = "Strong Lean UNDER"; verdictCol = "#ef4444"; }
    else if (score <= -2) { verdictLabel = "Lean UNDER"; verdictCol = "#f97316"; }
    else { verdictLabel = "No Clear Edge"; verdictCol = "#888"; }
    if (score > -3.5) return null;
    const baseL = drLine(avg);
    return { p, opp, avg, line, l5, hitRate, hits, total: vals.length, dvpPct, score,
             verdictLabel, verdictCol, games: vals.length, isCustom: line !== baseL };
  }
  function scoreOverLine(p, opp, line, venue) {
    if (!line || (p.runs || 0) < 12) return null;
    const curGames = batRows(p.name).filter(isCurWindow);
    if (curGames.length < 3) return null;
    const dvpPct = opp ? getDVPPct(opp, p.batRole || "MID", "runs") : null;
    if (dvpPct !== null && dvpPct <= 0) return null;
    const score = scoreCMP(p, "runs", line, opp, venue);
    if (score === null) return null;
    const avg = p.runs || 0;
    const vals = curGames.map(r => r.runs || 0);
    const hits = vals.filter(v => v >= line).length;
    const hitRate = hits / vals.length;
    const l5 = getL5Avg(p.name, "runs");
    let verdictLabel, verdictCol;
    if (score >= 5.5) { verdictLabel = "Green Light"; verdictCol = "#22c55e"; }
    else if (score >= 3.5) { verdictLabel = "Lean OVER"; verdictCol = "#86efac"; }
    else { verdictLabel = "No Clear Edge"; verdictCol = "#888"; }
    if (score < 5.5) return null;
    const baseL = drLine(avg);
    return { p, opp, avg, line, l5, hitRate, hits, total: vals.length, dvpPct, score,
             verdictLabel, verdictCol, games: vals.length, isCustom: line !== baseL };
  }

  // generic verdict for any market (Check My Bet / player modal)
  function verdict(p, statKey, line, opp, venue) {
    const score = scoreCMP(p, statKey, line, opp, venue);
    let label, col;
    if (score >= 5.5) { label = "Green Light OVER"; col = "#22c55e"; }
    else if (score >= 3.5) { label = "Lean OVER"; col = "#86efac"; }
    else if (score <= -3.5) { label = "Strong Lean UNDER"; col = "#ef4444"; }
    else if (score <= -2) { label = "Lean UNDER"; col = "#f97316"; }
    else { label = "No Clear Edge"; col = "#888"; }
    return { score, label, col };
  }

  // ---- verbose factor list for Check My Bet ----
  function cmpFactors(p, statKey, line, opp, lbl, venue) {
    lbl = lbl || statKey;
    const F = [];
    const push = (tone, text) => F.push({ tone, text });
    const allGames = rowsFor(p.name, statKey);
    const curGames = allGames.filter(isCurWindow);
    if (!allGames.length) { push("bad", "No innings in the log for this scope"); return F; }
    const curVals = curGames.map(r => statOf(r, statKey));
    const avg = curVals.length ? curVals.reduce((a, b) => a + b, 0) / curVals.length : (p[statKey] || 0);
    const hr = getHitRate(p.name, statKey, line, false);
    const hrCur = getHitRate(p.name, statKey, line, true);
    if (hr) {
      const pct = hr.rate * 100;
      const tone = pct >= 65 ? "good" : pct >= 50 ? "lean" : pct >= 35 ? "neutral" : "bad";
      push(tone, `Hit ${line}+ ${lbl} in ${Math.round(pct)}% of ${hr.n} innings (${SCOPE.format})`);
    }
    if (hrCur && hr && hrCur.n >= 5 && Math.abs(hrCur.rate - hr.rate) > 0.1)
      push(hrCur.rate > hr.rate ? "good" : "warn",
        `Last 12 months: ${Math.round(hrCur.rate * 100)}% over ${hrCur.n} innings — ${hrCur.rate > hr.rate ? "trending up" : "trending down"}`);
    const l5 = getL5Avg(p.name, statKey);
    if (l5 !== null) {
      const t = avg > 0 ? ((l5 - avg) / avg) * 100 : 0;
      push(l5 >= line ? "good" : l5 >= line * 0.8 ? "neutral" : "warn",
        `L5 average ${l5.toFixed(1)} ${lbl} (${t >= 0 ? "+" : ""}${t.toFixed(0)}% vs window avg ${avg.toFixed(1)})`);
    }
    let cvVal = null;
    if (allGames.length >= 5) {
      const vals = allGames.map(r => statOf(r, statKey));
      const mn = vals.reduce((a, b) => a + b, 0) / vals.length;
      if (mn > 0) cvVal = Math.sqrt(vals.reduce((s, v) => s + Math.pow(v - mn, 2), 0) / vals.length) / mn;
    }
    if (cvVal !== null) {
      const b = cvBands(statKey);
      if (cvVal < b.lo) push("good", `Consistent for the format — low variance (CV ${cvVal.toFixed(2)})`);
      else if (cvVal >= b.hi) push("neutral", `Boom-bust profile — high variance (CV ${cvVal.toFixed(2)}), typical of ${SCOPE.format} but size accordingly`);
    }
    const roleGroup = BOWL_KEYS.has(statKey) ? "BOWL" : (p.batRole || "MID");
    const dvpPct = opp ? getDVPPct(opp, roleGroup, statKey) : null;
    if (dvpPct !== null) {
      const rk = roleGroup !== "BOWL" ? getDVPRank(opp, roleGroup, statKey) : null;
      const rkTxt = rk ? ` (#${rk.rank}/${rk.total} softest)` : "";
      const rl = ROLE_LABEL[roleGroup] || roleGroup;
      if (dvpPct > 15) push("good", `${opp} concede ${dvpPct.toFixed(0)}% above avg ${lbl} to ${rl}s${rkTxt} — elite soft matchup`);
      else if (dvpPct > 5) push("lean", `${opp} concede ${dvpPct.toFixed(0)}% above avg ${lbl} to ${rl}s${rkTxt} — favourable`);
      else if (dvpPct > -5) push("neutral", `${opp} about average vs ${rl}s for ${lbl}${rkTxt}`);
      else if (dvpPct > -15) push("warn", `${opp} tough on ${rl}s for ${lbl}${rkTxt} — negative matchup`);
      else push("bad", `${opp} among the toughest vs ${rl}s for ${lbl}${rkTxt} — avoid`);
    }
    if (venue) {
      const vp = venuePct(venue, statKey === "wickets" ? "wktsPI" : statKey === "sixes" ? "sixesPI" : statKey === "fours" ? "foursPI" : "runsPI");
      if (vp !== null) {
        if (vp > 12) push("good", `${venue}: ${vp.toFixed(0)}% above ${SCOPE.format} average for ${lbl} — scoring ground`);
        else if (vp > 5) push("lean", `${venue}: +${vp.toFixed(0)}% for ${lbl}`);
        else if (vp < -12) push("warn", `${venue}: ${vp.toFixed(0)}% below average for ${lbl} — tough ground`);
        else push("neutral", `${venue}: near ${SCOPE.format} average for ${lbl}`);
      }
    }
    const vsOpp = opp ? allGames.filter(r => r.opp === opp) : [];
    if (vsOpp.length) {
      const last = vsOpp[vsOpp.length - 1];
      const lv = statOf(last, statKey);
      const hitsVs = vsOpp.filter(r => statOf(r, statKey) >= line).length;
      const hist = vsOpp.length > 1 ? ` · ${hitsVs}/${vsOpp.length} historical vs ${opp}` : "";
      if (lv >= line) push("good", `Last vs ${opp} (${last.date}): ${lv} ${lbl} — hit the line${hist}`);
      else push(lv >= line * 0.8 ? "neutral" : "warn", `Last vs ${opp} (${last.date}): ${lv} ${lbl} — ${lv >= line * 0.8 ? "just missed" : "below the line"}${hist}`);
    } else if (opp) push("neutral", `No previous innings vs ${opp} in log`);
    const sig = opp ? getContextSignals(p, statKey, opp) : [];
    sig.filter(s => Math.abs(s.pct) >= 5)
      .sort((a, b) => Math.abs(b.pct * b.weight) - Math.abs(a.pct * a.weight))
      .slice(0, 4).forEach(s => {
        const c = s.pct * s.weight;
        const tone = c > 15 ? "good" : c > 5 ? "lean" : c > -5 ? "neutral" : c > -15 ? "warn" : "bad";
        push(tone, `${opp} — ${s.label}: ${s.pct >= 0 ? "+" : ""}${s.pct.toFixed(0)}% vs ${SCOPE.format} avg`);
      });
    const windowHigh = curGames.reduce((mx, r) => Math.max(mx, statOf(r, statKey)), 0);
    if (windowHigh > 0 && windowHigh < line) {
      if (curGames.length >= 5) push("bad", `12-month high only ${windowHigh} — never hit this line in the window`);
      else push("lean", `12-month high only ${windowHigh} — but just ${curGames.length} innings in the window`);
    }
    return F;
  }

  /* ============== PLAYER INDEX & ROLES ============== */
  function classifyBat(rows) {
    const recent = rows.slice(-10).map(r => r.batOrder).filter(o => o != null);
    if (!recent.length) return null;
    const sorted = recent.slice().sort((a, b) => a - b);
    const med = sorted[Math.floor(sorted.length / 2)];
    return roleOfOrder(med);
  }
  function classifyBowl(rows) {
    // True phase usage when the pipeline provides ballsPP/ballsDeath (v3+);
    // falls back to phase wicket shares on older bundles.
    const balls = rows.reduce((s, r) => s + (r.ballsBowled || 0), 0);
    const bpp = rows.reduce((s, r) => s + (r.ballsPP || 0), 0);
    const bdth = rows.reduce((s, r) => s + (r.ballsDeath || 0), 0);
    if (balls >= 60 && (bpp + bdth) > 0) {
      if (bpp / balls >= 0.35) return "NEW";
      if (bdth / balls >= 0.35) return "DEATH";
      return "MID";
    }
    const w = rows.reduce((s, r) => s + (r.wickets || 0), 0);
    if (w < 8) return "GEN";
    const pp = rows.reduce((s, r) => s + (r.wktsPP || 0), 0);
    const dth = rows.reduce((s, r) => s + (r.wktsDeath || 0), 0);
    if (pp / w >= 0.4) return "NEW";
    if (dth / w >= 0.4) return "DEATH";
    return "MID";
  }
  function buildPlayers() {
    _players = []; _playerIdx = {};
    Object.keys(_byName).forEach(name => {
      const rows = rowsOf(name);
      if (!rows.length) return;
      const bR = rows.filter(r => r.bat), wR = rows.filter(r => r.bowl);
      const cur = rows.filter(isCurWindow);
      if (!cur.length) return;                       // active in window only
      const team = rows[rows.length - 1].team;
      const teams = [...new Set(rows.map(r => r.team))];
      const p = { name, team, teams, games: new Set(rows.map(r => r.matchId)).size };
      if (bR.length) {
        p.batRole = classifyBat(bR);
        const n = bR.length;
        p.runs = bR.reduce((s, r) => s + (r.runs || 0), 0) / n;
        p.balls = bR.reduce((s, r) => s + (r.balls || 0), 0) / n;
        p.fours = bR.reduce((s, r) => s + (r.fours || 0), 0) / n;
        p.sixes = bR.reduce((s, r) => s + (r.sixes || 0), 0) / n;
        p.sr = p.balls ? (p.runs / p.balls) * 100 : 0;
        p.batInnings = n;
      }
      if (wR.length) {
        p.bowlRole = classifyBowl(wR);
        const n = wR.length;
        p.wickets = wR.reduce((s, r) => s + (r.wickets || 0), 0) / n;
        p.econ = wR.reduce((s, r) => s + (r.econ || 0), 0) / n;
        p.dots = wR.reduce((s, r) => s + (r.dots || 0), 0) / n;
        p.runsConceded = wR.reduce((s, r) => s + (r.runsConceded || 0), 0) / n;
        p.maidens = wR.reduce((s, r) => s + (r.maidens || 0), 0) / n;
        p.bowlInnings = n;
      }
      p.isAllRounder = !!(bR.length >= 5 && wR.length >= 5 && p.batRole !== "LOW");
      p.role = p.batRole && p.bowlRole ? (p.isAllRounder ? "AR" : (bR.length >= wR.length ? p.batRole : "BOWL"))
             : p.batRole ? p.batRole : "BOWL";
      _players.push(p); _playerIdx[name] = p;
    });
    _players.sort((a, b) => (b.games || 0) - (a.games || 0));
  }

  /* ============== TEAM / ATTACK / VENUE TABLES ============== */
  function buildTables() {
    _teamProf = {}; _teamAvgs = {}; _attack = {}; _attackAvgs = {}; _venues = {}; _venueAvgs = {};
    const scoped = ROWS.filter(inScope);
    // per-team accumulators
    const T = {};
    const tGet = t => (T[t] = T[t] || { m: new Set(),
      br: 0, bb: 0,                     // own batting
      wk: 0, wb: 0, wr: 0,              // own bowling (taken/balls/conceded)
      fConc: 0, sConc: 0, wLost: 0,     // conceded to opponents / own wickets lost
      ppConcR: 0, ppConcB: 0, dthConcR: 0, dthConcB: 0,   // approx phase concessions
      ppR: 0, ppB: 0, dthR: 0, dthB: 0 });                // own phase batting
    // attack-vs-role accumulators: att[opp][role] = {runs,fours,sixes,inns}
    const att = {};
    // venue accumulators
    const V = {};
    scoped.forEach(r => {
      if (r.bat) {
        const own = tGet(r.team), opp = tGet(r.opp);
        own.m.add(r.matchId); opp.m.add(r.matchId);
        own.br += r.runs || 0; own.bb += r.balls || 0;
        own.ppR += r.runsPP || 0; own.dthR += r.runsDeath || 0;
        if (r.out) own.wLost += 1;
        opp.fConc += r.fours || 0; opp.sConc += r.sixes || 0;
        opp.ppConcR += r.runsPP || 0; opp.dthConcR += r.runsDeath || 0;
        const role = roleOfOrder(r.batOrder);
        const a = ((att[r.opp] = att[r.opp] || {})[role] = att[r.opp][role] || { runs: 0, fours: 0, sixes: 0, inns: 0 });
        a.runs += r.runs || 0; a.fours += r.fours || 0; a.sixes += r.sixes || 0; a.inns += 1;
        const vk = venueKey(r.venue);
        if (vk) {
          const v = (V[vk] = V[vk] || { name: r.venue, runs: 0, fours: 0, sixes: 0, wkts: 0, inns: new Set() });
          v.runs += r.runs || 0; v.fours += r.fours || 0; v.sixes += r.sixes || 0;
          if (r.out) v.wkts += 1;
          v.inns.add(r.matchId + "|" + r.innings);
        }
      }
      if (r.bowl) {
        const own = tGet(r.team);
        own.m.add(r.matchId);
        own.wk += r.wickets || 0; own.wb += r.ballsBowled || 0; own.wr += r.runsConceded || 0;
      }
    });
    const bpo = SCOPE.format === "T100" ? 5 : 6;
    Object.entries(T).forEach(([team, s]) => {
      const n = Math.max(1, s.m.size);
      _teamProf[team] = {
        matches: n,
        batSR: s.bb ? (s.br / s.bb) * 100 : null,
        bowlEcon: s.wb ? (s.wr / s.wb) * bpo : null,
        wktsPM: s.wk / n,
        wktsLostPM: s.wLost / n,
        foursConcPM: s.fConc / n,
        sixesConcPM: s.sConc / n,
        ppEconConc: s.ppConcR / n,     // PP runs conceded per match (proxy econ)
        deathEconConc: s.dthConcR / n,
        ppSR: s.ppR / n,               // own PP runs per match (tempo proxy)
        deathSR: s.dthR / n,
      };
    });
    const profKeys = ["batSR", "bowlEcon", "wktsPM", "wktsLostPM", "foursConcPM", "sixesConcPM", "ppEconConc", "deathEconConc", "ppSR", "deathSR"];
    profKeys.forEach(k => {
      const v = Object.values(_teamProf).map(t => t[k]).filter(x => x != null && isFinite(x));
      if (v.length) _teamAvgs[k] = v.reduce((a, b) => a + b, 0) / v.length;
    });
    // attack-vs-role per-innings averages + league avgs (min 12 innings sample)
    const roleAgg = {};
    Object.entries(att).forEach(([opp, roles]) => {
      _attack[opp] = {};
      Object.entries(roles).forEach(([role, a]) => {
        if (a.inns < 12) return;
        _attack[opp][role] = { runs: a.runs / a.inns, fours: a.fours / a.inns, sixes: a.sixes / a.inns, inns: a.inns };
        const g = (roleAgg[role] = roleAgg[role] || { runs: 0, fours: 0, sixes: 0, inns: 0 });
        g.runs += a.runs; g.fours += a.fours; g.sixes += a.sixes; g.inns += a.inns;
      });
    });
    Object.entries(roleAgg).forEach(([role, g]) => {
      _attackAvgs[role] = { runs: g.runs / g.inns, fours: g.fours / g.inns, sixes: g.sixes / g.inns };
    });
    // venue per-innings rates + averages (min 8 team-innings)
    Object.entries(V).forEach(([vk, v]) => {
      const n = v.inns.size;
      if (n < 8) return;
      _venues[vk] = { name: v.name, runsPI: v.runs / n, foursPI: v.fours / n, sixesPI: v.sixes / n, wktsPI: v.wkts / n, inns: n };
    });
    ["runsPI", "foursPI", "sixesPI", "wktsPI"].forEach(k => {
      const v = Object.values(_venues).map(x => x[k]).filter(x => x != null);
      if (v.length) _venueAvgs[k] = v.reduce((a, b) => a + b, 0) / v.length;
    });
  }

  /* ============== CONFIGURE ============== */
  function configure(ctx) {
    ROWS = (ctx.rows || (ctx.logs && ctx.logs.rows) || []).slice()
      .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
    TEAMS = ctx.teams || (ctx.stats && ctx.stats.teams) || {};
    RATINGS = ctx.ratings || {};
    if (ctx.scope) SCOPE = Object.assign({ format: "T20", level: null }, ctx.scope);
    const cd = new Date(); cd.setMonth(cd.getMonth() - 12);
    _winCutoff = ctx.windowCutoff || cd.toISOString().slice(0, 10);
    _byName = {};
    ROWS.forEach(r => { (_byName[r.name] = _byName[r.name] || []).push(r); });
    rebuild();
  }
  function setScope(scope) {
    SCOPE = Object.assign({}, SCOPE, scope);
    rebuild();
  }
  function rebuild() {
    Object.keys(_ctxSigCache).forEach(k => delete _ctxSigCache[k]);
    Object.keys(_l5Cache).forEach(k => delete _l5Cache[k]);
    buildTables();
    buildPlayers();
  }

  // Attack-vs-role grid rows: every team with data, pct vs league avg per role.
  function attackEntries(statKey) {
    return Object.keys(_attack).map(team => {
      const e = { team };
      ["OP", "TOP", "MID", "LOW"].forEach(role => { e[role] = getDVPPct(team, role, statKey); });
      e.inns = Object.values(_attack[team]).reduce((s, a) => s + (a.inns || 0), 0);
      return e;
    }).filter(e => ["OP","TOP","MID","LOW"].some(r => e[r] != null));
  }
  // Venue board rows, vs-average pcts included.
  function venueEntries() {
    return Object.values(_venues).map(v => ({
      name: v.name, inns: v.inns,
      runsPI: v.runsPI, foursPI: v.foursPI, sixesPI: v.sixesPI, wktsPI: v.wktsPI,
      runsPct: _venueAvgs.runsPI ? ((v.runsPI - _venueAvgs.runsPI) / _venueAvgs.runsPI) * 100 : null,
      sixesPct: _venueAvgs.sixesPI ? ((v.sixesPI - _venueAvgs.sixesPI) / _venueAvgs.sixesPI) * 100 : null,
      wktsPct: _venueAvgs.wktsPI ? ((v.wktsPI - _venueAvgs.wktsPI) / _venueAvgs.wktsPI) * 100 : null,
    }));
  }
  // Pipeline-built quality tier (ELITE/STRONG/MID) for the active format.
  function tierOf(name, kind) {
    const t = ((RATINGS.tiers || {})[SCOPE.format] || {})[kind === "bowl" ? "bowl" : "bat"];
    return t ? (t[name] || null) : null;
  }
  // Bunny check: who owns this batter? Needs dismissedBy (pipeline v3 logs).
  function bunnyInfo(batterName, opp) {
    const games = batRows(batterName).filter(r => !opp || r.opp === opp);
    if (!games.length || games[0].dismissedBy === undefined) return null;
    const by = {};
    games.forEach(r => { if (r.dismissedBy) by[r.dismissedBy] = (by[r.dismissedBy] || 0) + 1; });
    const top = Object.entries(by).sort((a, b) => b[1] - a[1])[0];
    if (!top) return { top: null, inns: games.length };
    return { top: { bowler: top[0], n: top[1] }, inns: games.length };
  }
  function players() { return _players; }
  function playersForTeam(t) { return _players.filter(p => p.teams && p.teams.includes(t)); }
  function playerByName(n) { return _playerIdx[n] || null; }
  function scope() { return Object.assign({}, SCOPE); }
  function teamProfile(t) { return _teamProf[t] || null; }
  function venueProfile(v) { const vk = venueKey(v); return vk ? (_venues[vk] || null) : null; }

  return {
    configure, setScope, scope, players, playersForTeam, playerByName, teamProfile, venueProfile,
    scoreCMP, cmpFactors, getContextSignals, scoreOverLine, scoreUnderLine,
    verdict, drLine, getDVPPct, getDVPRank, muPct: muPctFor, muInfo,
    getL5Avg, getRecentAvg, getHitRate, venuePct,
    attackEntries, venueEntries, tierOf, bunnyInfo,
    POS_TO_DVP, ROLE_LABEL,
  };
})();
