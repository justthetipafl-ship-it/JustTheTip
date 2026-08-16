/* ============================================================================
   JTT NFL — scoring.js (window.JTTScoring)
   Ported from the AFL engine; same public surface, NFL signal matrix v1.

   Engine extensions vs AFL (per approved Signal Weight Matrix v1):
     OWN  — player's own-team percentile (pace / pass rate / scoring).
     ENV  — per-fixture environment: spread, total, wind, rest, QB status.
            Shell precomputes ctx.env = { TEAM: {spread,total,wind,roof,
            restDiff,qbStatus,home} } from odds/weather/fixture/lineups.
            spread: negative = TEAM favored. qbStatus: 'Q'|'D'|'O'|'BACKUP'|null.
   ENV magnitudes are mapped onto the AFL ±% signal scale (centred on 0) with
   the doc's fire-thresholds preserved (wind fires ≥20 km/h outdoors, script
   fires |spread| ≥ 1, rest fires |diff| ≥ 2 days).

   PLAYER (addI) signals anchor to the market's natural opponent `_a` base and
   scale it by the player's affinity vs their positional pool (clamped 0.5–1.6)
   — identical mechanics to AFL's addI.

   Fixes carried into the port (latent in AFL):
     • configure() assigns TD = ctx.teams, so season-path add() signals fire.
     • gamelogs carry Opp, so last-vs-opponent logic actually matches.

   Representative-game filter: snapPct >= 25 (AFL's TOG>=50 analogue).
   Unknown snap (null / <= 0 from a missed join) is kept, mirroring AFL's
   keep-on-unknown semantics.
   ========================================================================== */
window.JTTScoring = (function () {
  "use strict";

  // ---- module data (set by configure) ----
  let PD = [], TD = [], TF = [], DVP = [], _dvpIdx = {}, CUR = "2025";
  let BA = {}, ENV = {};
  const _ctxSigCache = {}, _l5Cache = {}, _pdPoolAvgs = {};

  // ---- constants ----
  const POS_TO_DVP = { QB: 'QB', RB: 'RB', WR: 'WR', TE: 'TE' };
  // statKey -> DVP table field (identity — DVP rows use internal keys)
  const DVP_STAT_MAP = {
    passYds:'passYds', passAtt:'passAtt', passComp:'passComp', passTds:'passTds',
    passInt:'passInt', rushYds:'rushYds', rushAtt:'rushAtt', rushTds:'rushTds',
    receptions:'receptions', recYds:'recYds', recTds:'recTds',
    rushRecYds:'rushRecYds', anytimeTd:'anytimeTd', fanPts:'fanPts', targets:'targets'
  };
  // stat -> its "_a" (allowed) team key for muPct in scoreCMP.
  // recYds/recTds ride the team passing keys; composites are DVP-anchored.
  const ALL = [
    {k:'passYds', a:'passYds_a'}, {k:'passAtt', a:'passAtt_a'},
    {k:'passComp',a:'passComp_a'},{k:'passTds', a:'passTds_a'},
    {k:'passInt', a:'passInt_a'}, {k:'rushYds', a:'rushYds_a'},
    {k:'rushAtt', a:'rushAtt_a'}, {k:'rushTds', a:'rushTds_a'},
    {k:'receptions',a:'receptions_a'}, {k:'targets', a:'targets_a'},
    {k:'recYds',  a:'passYds_a'}, {k:'recTds',  a:'passTds_a'},
    {k:'rushRecYds', a:null}, {k:'anytimeTd', a:null}, {k:'fanPts', a:'points_a'}
  ];
  function getStat(k){ return ALL.find(s=>s.k===k); }

  // per-market minimum season average — gates Green Lights / Death Riders
  const MIN_AVG = {
    passYds:150, passAtt:20, passComp:12, passTds:0.8, passInt:0.4,
    rushYds:25, rushAtt:6, rushTds:0.25, receptions:2, recYds:25, recTds:0.25,
    rushRecYds:30, fanPts:6, anytimeTd:0.25,
    longRec:12, longRush:6, longComp:20,
    tackles:3.5, soloTk:2, astTk:1, tfl:0.3, defSacks:0.15
  };

  // ---- accessors (bound to split-data model) ----
  // Representative-game filter: blowout garbage-time cameos and injury-shortened
  // games (low snap share) would otherwise drag averages and inflate variance.
  // AFL gated on TOG>=50; NFL analogue is snapPct>=25. Unknown snap kept.
  const SNAP_MIN = 25;
  function _isValidSnapGame(r){
    if(!r) return true;
    const raw = r.snapPct != null ? r.snapPct : null;
    if(raw == null) return true;
    const t = parseFloat(raw);
    if(isNaN(t) || t <= 0) return true;   // 0 = missed snap join, not a real DNP
    return t >= SNAP_MIN;
  }
  function dvpByName(name){ return (_dvpIdx[name]||[]).filter(_isValidSnapGame); }
  function dvpByNameRaw(name){ return _dvpIdx[name] || []; }
  function isCurSeason(r){ return String(r.Year) === CUR; }
  function getPlayerPos(p){ return p.position; }
  function teamAbbrev(t){ return t; }    // data uses nflverse abbreviations throughout
  function pdToLogKey(k){ return k; }    // players + logs share internal keys

  // ---- matchup colour bucket ----
  function muInfo(pct){
    if(pct===null||pct===undefined) return null;
    if(pct>15)  return {t:'Soft',    c:'#22c55e', cl:'c-soft'};
    if(pct>5)   return {t:'Fav',     c:'#eab308', cl:'c-fav'};
    if(pct>-5)  return {t:'Neutral', c:'#555',    cl:'c-neu'};
    if(pct>-15) return {t:'Tough',   c:'#f97316', cl:'c-tough'};
    return             {t:'V.Tough', c:'#ef4444', cl:'c-vtough'};
  }

  // ---- DVP (team x position allowed vs league avg) ----
  function getDVP(oppTeam, posGroup, statKey){
    const rec = DVP.find(d=>d.team===oppTeam && d.pos===posGroup);
    if(!rec) return null;
    return rec[statKey]==null?null:rec[statKey];
  }
  function getDVPAvg(posGroup, statKey){
    const recs = DVP.filter(d=>d.pos===posGroup);
    if(!recs.length) return null;
    const vals = recs.map(d=>d[statKey]).filter(v=>v!=null);
    return vals.length ? vals.reduce((a,b)=>a+b,0)/vals.length : null;
  }
  function getDVPPct(oppTeam, posGroup, statKey){
    const val = getDVP(oppTeam, posGroup, statKey);
    const avg = getDVPAvg(posGroup, statKey);
    if(val===null||avg===null||avg===0) return null;
    return ((val-avg)/avg)*100;
  }

  // ---- team allowed / own-team / form percentages ----
  function muPct(opp, akey){
    if(!akey) return null;
    const td = TD.find(t=>t.team===opp); if(!td) return null;
    const v = td[akey], avg = BA[akey];
    return (avg && v!=null) ? ((v-avg)/avg)*100 : null;
  }
  function tfPctFor(opp, tfKey){
    if(!TF || !TF.length || !TF._avgs) return null;
    const t = TF.find(x=>x.team===opp); if(!t) return null;
    const avg = TF._avgs[tfKey];
    if(!avg || t[tfKey]==null) return null;
    return ((t[tfKey]-avg)/avg)*100;
  }
  // OWN: same percentile math against the player's own team (form preferred)
  function ownPct(team, key){
    const tf = tfPctFor(team, key);
    if(tf!==null) return tf;
    return muPct(team, key);
  }
  function envFor(team){ return ENV[team] || null; }

  // ---- recent-form helpers ----
  function getL5Avg(name, statKey){
    const key = name+'|'+statKey;
    if(key in _l5Cache) return _l5Cache[key];
    const games = dvpByName(name);
    let v = null;
    if(games.length>=2){ const r=games.slice(-5).map(g=>g[statKey]||0); v=r.reduce((a,b)=>a+b,0)/r.length; }
    _l5Cache[key]=v; return v;
  }
  function getRecentAvg(name, statKey, n){
    if(n===0) return null;
    const games = dvpByName(name); if(!games.length) return null;
    const r = games.slice(-n).map(g=>g[statKey]||0); if(!r.length) return null;
    return r.reduce((a,b)=>a+b,0)/r.length;
  }
  function getHitRate(name, statKey, line, curOnly){
    let games = dvpByName(name);
    if(curOnly) games = games.filter(isCurSeason);
    if(games.length<3) return null;
    const hits = games.filter(g=>(g[statKey]||0)>=line).length;
    return {rate:hits/games.length, n:games.length};
  }

  // ---- position-pool averages (affinity), usage fields only ----
  function buildPDPoolAvgs(){
    Object.keys(_pdPoolAvgs).forEach(k=>delete _pdPoolAvgs[k]);
    const groups = {};
    PD.forEach(p=>{ const pg=POS_TO_DVP[p.position]||p.position||'Unknown'; (groups[pg]=groups[pg]||[]).push(p); });
    const POOL = { tgtShare:'tgtShare', snapPct:'snapPct', rzTgt:'rzTgt',
      rzCarry:'rzCarry', glCarry:'glCarry', ypc:'ypc', aDot:'aDot',
      carryShare:'rushAtt', touches:'touches', passInt:'passInt' };
    Object.entries(groups).forEach(([pg, players])=>{
      _pdPoolAvgs[pg]={};
      Object.entries(POOL).forEach(([poolKey, field])=>{
        const vals=players.map(p=>+(p[field]||0)).filter(v=>v>0);
        if(vals.length) _pdPoolAvgs[pg][poolKey]=vals.reduce((a,b)=>a+b,0)/vals.length;
      });
    });
  }
  function aliasPlayer(p){
    p.touches = (+p.rushAtt||0) + (+p.targets||0);
    return p;
  }
  function _avgsOf(rows, suffixOnly){
    const a={}; if(!rows||!rows.length) return a;
    const keys = Object.keys(rows[0]).filter(k=>{
      if(typeof rows[0][k] !== 'number') return false;
      return suffixOnly ? k.endsWith('_a') : true;
    });
    keys.forEach(k=>{ const v=rows.map(r=>r[k]).filter(x=>x!=null&&typeof x==='number'); if(v.length)a[k]=v.reduce((s,x)=>s+x,0)/v.length; });
    return a;
  }

  /* ============== CONTEXT SIGNALS — NFL MATRIX V1 (approved) ============== */
  function getContextSignals(p, statType, opp){
    const cacheKey=(p.name||'')+'|'+(statType||'')+'|'+(opp||'');
    if(_ctxSigCache[cacheKey]) return _ctxSigCache[cacheKey];
    const _r=_getContextSignalsInner(p, statType, opp);
    _ctxSigCache[cacheKey]=_r; return _r;
  }
  function _getContextSignalsInner(p, statType, opp){
    const pos = p.position||'';
    const team = p.team||'';
    const env = envFor(team);
    const signals=[];

    // OPP: opponent-allowed percentile; team-form preferred over season
    function add(label, aKey, weight){
      const tf = aKey?tfPctFor(opp, aKey):null;
      const td = aKey?muPct(opp, aKey):null;
      const pct = tf!==null?tf:td;
      if(pct!==null) signals.push({label, pct, weight, src:tf!==null?'form':'season'});
    }
    // OWN: player's own-team percentile
    function addOwn(label, key, weight){
      const pct = ownPct(team, key);
      if(pct!==null) signals.push({label, pct, weight, src:'own'});
    }
    // PLAYER: opponent base x player affinity vs positional pool
    function addI(label, aKey, weight, playerVal, poolKey){
      const tf = aKey?tfPctFor(opp, aKey):null;
      const td = aKey?muPct(opp, aKey):null;
      const oppPct = tf!==null?tf:td;
      if(oppPct===null) return;
      const pg = POS_TO_DVP[p.position]||p.position||'';
      const pool = _pdPoolAvgs[pg]||{};
      const poolAvg = pool[poolKey]||null;
      const pVal = typeof playerVal==='number'?playerVal:(+(p[playerVal]||0));
      let affinity=1.0;
      if(poolAvg&&poolAvg>0){ const rel=(pVal-poolAvg)/poolAvg; affinity=Math.max(0.5,Math.min(1.6,1+rel)); }
      const adjPct=oppPct*affinity;
      const tag=affinity>1.25?' (strength match)':affinity<0.75?' (poor fit)':'';
      signals.push({label:label+tag, pct:adjPct, weight, src:tf!==null?'form+player':'season+player'});
    }
    // ENV: per-fixture environment; magnitude on the centred ±% scale
    function addEnv(label, pct, weight){
      if(pct===null||pct===undefined||!isFinite(pct)) return;
      signals.push({label, pct, weight, src:'env'});
    }
    // ---- ENV percentile math (doc thresholds preserved) ----
    const E = env || {};
    const total = (E.total!=null) ? ((E.total - 46) / 46) * 100 : null;           // league-avg total ~46
    const sp = (E.spread!=null && Math.abs(E.spread)>=1) ? E.spread : null;
    const fav = sp!==null ? Math.max(-28, Math.min(28, -sp*2)) : null;            // favored → positive
    const dog = fav!==null ? -fav : null;                                          // trailing script
    const outdoors = !E.roof || E.roof==='outdoors' || E.roof==='open';
    const wind = (E.wind!=null && E.wind>=20 && outdoors)
                 ? 10 + Math.min(Math.max(E.wind-20,0),30)/30*30 : null;          // 10..40, sign via weight
    const rest = (E.restDiff!=null && Math.abs(E.restDiff)>=2)
                 ? Math.max(-7, Math.min(7, E.restDiff))*3 : null;                // ±21
    const qb = E.qbStatus==='Q' ? 25 : (E.qbStatus==='D'||E.qbStatus==='O'||E.qbStatus==='BACKUP') ? 40 : null;
    const home = E.home ? 8 : null;
    const lblTotal = 'Game Total'+(E.total!=null?(' '+E.total):'');
    const lblFav = 'Script: protecting'+(sp!==null?(' ('+(-sp)+')'):'');
    const lblDog = 'Script: chasing'+(sp!==null?(' (+'+sp+')'):'');
    const lblWind = 'Wind'+(E.wind!=null?(' '+Math.round(E.wind)+' km/h'):'');
    const lblRest = 'Rest edge'+(E.restDiff!=null?(' '+(E.restDiff>0?'+':'')+E.restDiff+'d'):'');
    const lblQb = 'QB status: '+(E.qbStatus||'');

    /* ---------------- PASSING (QB) ---------------- */
    if(statType==='passYds' && pos==='QB'){
      add('Pass Yds Allowed','passYds_a',1.2);
      addOwn('Own Pass Volume','passAtt',0.8);
      addEnv(lblTotal,total,0.7);
      addEnv(lblDog,dog,0.6);
      add('Opp Pace','plays_a',0.5);
      add('Opp Takeaways','passInt_a',-0.4);
      add('Opp Pass Rush','sacks_a',-0.9);
      addEnv(lblWind,wind,-0.8);
    }
    if(statType==='passAtt' && pos==='QB'){
      addEnv(lblDog,dog,1.2);
      addOwn('Own Pass Volume','passAtt',1.0);
      add('Att Allowed','passAtt_a',0.8);
      add('Opp Pace','plays_a',0.7);
      add('Run Funnel','rushYds_a',-0.5);
      addEnv(lblTotal,total,0.4);
      addEnv(lblWind,wind,-0.3);
    }
    if(statType==='passComp' && pos==='QB'){
      add('Completions Allowed','passComp_a',1.1);
      addOwn('Own Pass Volume','passAtt',0.9);
      addEnv(lblDog,dog,0.5);
      add('Opp Pace','plays_a',0.4);
      addEnv(lblWind,wind,-0.5);
    }
    if(statType==='passTds' && pos==='QB'){
      add('Pass TDs Allowed','passTds_a',1.3);
      addEnv(lblTotal,total,1.0);
      addOwn('Own Scoring','points',0.8);
      add('Opp Takeaways','passInt_a',-0.3);
      addEnv(lblWind,wind,-0.7);
    }
    if(statType==='passInt' && pos==='QB'){
      add('INTs Generated','passInt_a',1.3);
      add('Opp Pass Rush','sacks_a',0.7);
      addEnv(lblDog,dog,0.6);
      addOwn('Own Pass Volume','passAtt',0.5);
      addEnv(lblWind,wind,0.4);
    }

    /* ---------------- RUSHING (RB / QB) ---------------- */
    if(statType==='rushYds' && (pos==='RB'||pos==='QB')){
      const rb = pos==='RB';
      add('Rush Yds Allowed','rushYds_a', rb?1.2:0.7);
      addI('Carry Share','rushAtt_a', rb?0.8:1.2, p.rushAtt, 'carryShare');
      if(rb) addEnv(lblFav,fav,0.9);
      addOwn('Own Rush Volume','rushAtt', rb?0.8:0.3);
      addI('Explosiveness','rushYds_a', rb?0.4:0.3, p.ypc, 'ypc');
      add('Opp Pace','plays_a', rb?0.4:0.2);
      if(!rb) add('Opp Pass Rush','sacks_a',0.3);
      addEnv(lblWind,wind,0.2);
    }
    if(statType==='rushAtt' && (pos==='RB'||pos==='QB')){
      const rb = pos==='RB';
      if(rb) addEnv(lblFav,fav,1.2);
      addI('Carry Share','rushAtt_a', rb?1.0:1.2, p.rushAtt, 'carryShare');
      addOwn('Own Rush Volume','rushAtt', rb?1.0:0.3);
      add('Att Allowed','rushAtt_a', rb?0.8:0.4);
      add('Opp Pace','plays_a', rb?0.5:0.2);
      addEnv(lblWind,wind, rb?0.3:0.2);
    }
    if(statType==='rushTds' && (pos==='RB'||pos==='QB')){
      const rb = pos==='RB';
      addI('Goal-Line Role','rushTds_a', rb?1.3:1.1, p.glCarry, 'glCarry');
      add('Rush TDs Allowed','rushTds_a', rb?1.2:0.8);
      addOwn('Own Scoring','points', rb?0.8:0.6);
      addEnv(lblFav,fav, rb?0.7:0.3);
      addEnv(lblTotal,total, rb?0.5:0.4);
    }

    /* ---------------- RECEIVING (RB / WR / TE) ---------------- */
    const recv = pos==='RB'||pos==='WR'||pos==='TE';
    if(statType==='receptions' && recv){
      addI('Target Share','receptions_a',1.2, p.tgtShare, 'tgtShare');
      add('Receptions Allowed','receptions_a',1.1);
      addOwn('Own Pass Volume','passAtt',0.9);
      addEnv(lblDog,dog, pos==='RB'?0.8:0.6);
      add('Opp Pass Rush','sacks_a', pos==='RB'?0.4:(pos==='WR'?-0.3:0.2));
      add('Opp Pace','plays_a',0.5);
      addEnv(lblQb,qb,-0.8);
    }
    if(statType==='recYds' && recv){
      addI('Target Share','passYds_a',1.1, p.tgtShare, 'tgtShare');
      add('Pass Yds Allowed','passYds_a',1.0);
      addOwn('Own Pass Volume','passAtt',0.8);
      addEnv(lblTotal,total,0.6);
      if(pos!=='RB') addI('Depth of Target','passYds_a', pos==='WR'?0.5:0.3, p.aDot, 'aDot');
      addEnv(lblDog,dog,0.5);
      add('Opp Pass Rush','sacks_a', pos==='RB'?0.2:(pos==='WR'?-0.4:-0.2));
      addEnv(lblWind,wind, pos==='RB'?-0.2:(pos==='WR'?-0.6:-0.4));
      addEnv(lblQb,qb,-0.9);
    }
    if(statType==='recTds' && recv){
      addI('Red-Zone Targets','passTds_a',1.3, p.rzTgt, 'rzTgt');
      add('Pass TDs Allowed','passTds_a',1.2);
      addEnv(lblTotal,total,0.8);
      addOwn('Own Scoring','points',0.7);
      addI('Target Share','receptions_a',0.6, p.tgtShare, 'tgtShare');
      addEnv(lblWind,wind, pos==='RB'?-0.3:(pos==='WR'?-0.5:-0.4));
      addEnv(lblQb,qb,-0.8);
    }

    /* ---------------- COMBINED ---------------- */
    if(statType==='rushRecYds' && recv){
      addI('Touch Volume','plays_a',1.0, p.touches, 'touches');
      addI('Snap Share','plays_a',0.8, p.snapPct, 'snapPct');
      addOwn('Own Pace','plays',0.6);
      addEnv(lblTotal,total,0.6);
      add('Opp Pace','plays_a',0.5);
      if(pos==='RB') addEnv(lblFav,fav,0.2);
      addEnv(lblQb,qb, pos==='RB'?-0.4:-0.7);
      if(pos!=='RB') addEnv(lblWind,wind, pos==='WR'?-0.4:-0.3);
    }
    if(statType==='anytimeTd' && (pos==='QB'||recv)){
      const rz = (pos==='QB'||pos==='RB');
      addI('Red-Zone Role', rz?'rushTds_a':'passTds_a', pos==='QB'?1.2:1.3,
           rz?p.glCarry:p.rzTgt, rz?'glCarry':'rzTgt');
      add('TDs Allowed', rz?'rushTds_a':'passTds_a', 1.1);
      addOwn('Own Scoring','points',0.9);
      addEnv(lblTotal,total,0.8);
      addEnv(lblFav,fav, pos==='RB'?0.5:(pos==='QB'?0.4:0.3));
      if(pos!=='QB') addEnv(lblQb,qb, pos==='RB'?-0.3:-0.7);
    }
    if(statType==='fanPts' && (pos==='QB'||recv)){
      addI('Snap Share','plays_a',0.9, p.snapPct, 'snapPct');
      add('Points Allowed','points_a',0.8);
      addEnv(lblTotal,total,0.7);
      addOwn('Own Pace','plays',0.6);
      if(pos!=='QB') addEnv(lblQb,qb, pos==='RB'?-0.3:-0.6);
    }

    // universal home nudge (doc: listed once, 0.1 everywhere)
    addEnv('Home', home, 0.1);
    return signals;
  }

  // Death Riders default line: round of average, gated by market minimum
  function drLine(avg, statKey){
    const min = MIN_AVG[statKey!=null?statKey:'recYds'] || 0;
    if(avg==null || avg < min) return null;
    return Math.round(avg);
  }

  // ---- adaptive form window: this season once >=10 games played this season, else last 10 across seasons ----
  function _formGames(name){ var all=dvpByName(name), sea=all.filter(isCurSeason); return sea.length>=10 ? sea : all.slice(-10); }
  function _formAvg(name, statKey, p){
    var all=dvpByName(name), sea=all.filter(isCurSeason);
    if(sea.length>=10 && p && p[statKey]!=null) return +p[statKey];
    var w=sea.length>=10 ? sea : all.slice(-10);
    var v=w.map(function(g){return +(g[statKey]||0);}).filter(function(x){return isFinite(x);});
    return v.length ? v.reduce(function(a,b){return a+b;},0)/v.length : 0;
  }
  function scoreCMP(p, statKey, line, opp){
    const logKey=pdToLogKey(statKey);
    const allGames=dvpByName(p.name);
    const gamesCur=_formGames(p.name);
    const logGames=allGames;
    const pdAvg=_formAvg(p.name, statKey, p);
    const avgCurVals=gamesCur.map(r=>r[logKey]||0);
    const avg=avgCurVals.length?avgCurVals.reduce((a,b)=>a+b,0)/avgCurVals.length:pdAvg;
    const l5=getL5Avg(p.name,statKey);
    const _l3g=allGames.slice(-3);
    const l3=_l3g.length>=3?_l3g.reduce((s,r)=>s+(r[logKey]||0),0)/_l3g.length:null;
    const seasonHigh=gamesCur.reduce((mx,r)=>Math.max(mx,r[logKey]||0),0);
    const hits=logGames.filter(r=>(r[logKey]||0)>=line).length;
    const hitRateRaw=logGames.length?hits/logGames.length:null;
    const hitRateCurRaw=gamesCur.length?gamesCur.filter(r=>(r[logKey]||0)>=line).length/gamesCur.length:null;
    const _blendN=gamesCur.length;
    const _blendW=_blendN/(_blendN+8);
    const hitRate=hitRateCurRaw!==null&&hitRateRaw!==null?_blendW*hitRateCurRaw+(1-_blendW)*hitRateRaw:hitRateRaw;
    const hitRateCur=hitRateCurRaw;
    let cv=null;
    if(logGames.length>=5){const vals=logGames.map(r=>r[logKey]||0);const mn=vals.reduce((a,b)=>a+b,0)/vals.length;if(mn>0)cv=Math.sqrt(vals.reduce((s,v)=>s+Math.pow(v-mn,2),0)/vals.length)/mn;}
    const avgGap=avg-line;
    const l5Gap=l5!==null?l5-line:null;
    const trend=l5!==null&&avg>0?((l5-avg)/avg)*100:null;
    const dvpPos=POS_TO_DVP[getPlayerPos(p)]||getPlayerPos(p);
    const dvpSk=DVP_STAT_MAP[statKey];
    const dvpPct=opp&&dvpSk?getDVPPct(teamAbbrev(opp),dvpPos,dvpSk):null;
    const muStatKey=(getStat(statKey)||{}).a;
    const muPctVal=opp&&muStatKey?muPct(opp,muStatKey):null;
    const muI=muInfo(muPctVal);
    const vsOppGames=opp?allGames.filter(r=>(r.Opp||'')===opp):[];
    const lastVsOpp=vsOppGames.slice().sort((a,b)=>{
      const ya=parseInt(a.Year||0),yb=parseInt(b.Year||0);
      if(ya!==yb)return yb-ya;
      return (b.Week||0)-(a.Week||0);
    })[0]||null;
    const lastVsOppVal=lastVsOpp?(lastVsOpp[logKey]||0):null;
    const allSignals=opp?getContextSignals(p,statKey,opp):[];

    // avgGap thresholds scale with the market: 5/1 units suit yardage markets,
    // count markets (receptions, TDs, attempts) use a relative gap instead.
    const YARDS=new Set(['passYds','rushYds','recYds','rushRecYds']);
    const gapBig = YARDS.has(statKey)?5:(avg>0?avg*0.2:1);
    const gapSmall = YARDS.has(statKey)?1:(avg>0?avg*0.05:0.25);

    let score=0;
    if(hitRate!==null){
      if(hitRate>=0.65) score+=3;
      else if(hitRate>=0.5) score+=1.5;
      else if(hitRate<0.35) score-=2.5;
    }
    if(avgGap>gapBig) score+=1;
    else if(avgGap>gapSmall) score+=0.3;
    else if(avgGap<=-gapSmall) score-=2;
    if(l3!==null&&avg>0){
      const l3Pct=((l3-avg)/avg)*100;
      if(l3Pct>15) score+=1.5;
      else if(l3Pct>5) score+=0.8;
      else if(l3Pct<-15) score-=1.5;
      else if(l3Pct<-5) score-=0.8;
    }
    if(l5!==null&&logGames.length>=5){
      if(l5Gap!==null&&l5Gap>0&&trend!==null&&trend>5) score+=1.5;
      else if(l5Gap!==null&&l5Gap<0&&trend!==null&&trend<-10) score-=1.5;
    }
    if(hitRateCur!==null&&gamesCur.length>=5&&Math.abs(hitRateCur-(hitRate||0))>0.1){
      if(hitRateCur>(hitRate||0)) score+=0.5; else score-=0.5;
    }
    if(cv!==null){
      if(cv<0.35) score+=0.8;
      else if(cv>=0.45) score-=0.3;
    }
    if(dvpPct!==null){
      if(dvpPct>15) score+=3;
      else if(dvpPct>5) score+=1.5;
      else if(dvpPct<-15) score-=3;
      else if(dvpPct<-5) score-=1.5;
    }
    if(muI&&opp){
      if(muI.t==='Soft'||muI.t==='Fav') score+=0.5;
      else if(muI.t==='Tough'||muI.t==='V.Tough') score-=0.5;
    }
    if(lastVsOpp&&opp&&lastVsOppVal!==null){
      if(lastVsOppVal>=line) score+=0.8;
      else if(lastVsOppVal<line*0.8) score-=0.5;
    }
    if(opp&&allSignals.length){
      allSignals.filter(s=>Math.abs(s.pct)>=5).sort((a,b)=>Math.abs(b.pct*b.weight)-Math.abs(a.pct*a.weight)).slice(0,5).forEach(s=>{
        const contribution=s.pct*s.weight;
        score+=contribution>0?Math.min(contribution*0.06,2):Math.max(contribution*0.06,-2);
      });
    }
    if(seasonHigh>0&&seasonHigh<line&&gamesCur.length>=5) score-=4;
    return score;
  }

  // ---- over / under wrappers (generalised per market for NFL) ----
  function scoreUnderLine(p, opp, line, statKey){
    statKey=statKey||'recYds';
    const min=MIN_AVG[statKey]||0;
    if(!line) return null;
    const gamesCur=_formGames(p.name);
    if(gamesCur.length<3) return null;
    const _favg=_formAvg(p.name,statKey,p); if(_favg<min*0.7) return null;
    const dvpPos=POS_TO_DVP[getPlayerPos(p)]||getPlayerPos(p);
    const dvpSk=DVP_STAT_MAP[statKey];
    const dvpPct=dvpSk&&opp?getDVPPct(teamAbbrev(opp),dvpPos,dvpSk):null;
    if(dvpPct!==null&&dvpPct>=0) return null;
    const score=scoreCMP(p,statKey,line,opp);
    const avg=_favg;
    const vals=gamesCur.map(r=>r[statKey]||0);
    const hits=vals.filter(v=>v>=line).length;
    const hitRate=hits/vals.length;
    const l5=getL5Avg(p.name,statKey);
    let verdictLabel,verdictCol;
    if(score<=-3.5){verdictLabel='Strong Lean UNDER';verdictCol='#ef4444';}
    else if(score<=-2){verdictLabel='Lean UNDER';verdictCol='#f97316';}
    else {verdictLabel='No Clear Edge';verdictCol='#888';}
    if(score>-3.5) return null;
    const baseL=drLine(avg,statKey);
    return {p,opp,statKey,avg,line,l5,hitRate,hits,total:vals.length,dvpPct,score,verdictLabel,verdictCol,gamesCur:vals.length,isCustom:line!==baseL};
  }
  function scoreOverLine(p, opp, line, statKey){
    statKey=statKey||'recYds';
    const min=MIN_AVG[statKey]||0;
    if(!line) return null;
    const gamesCur=_formGames(p.name);
    if(gamesCur.length<3) return null;
    const _favg=_formAvg(p.name,statKey,p); if(_favg<min) return null;
    const dvpPos=POS_TO_DVP[getPlayerPos(p)]||getPlayerPos(p);
    const dvpSk=DVP_STAT_MAP[statKey];
    const dvpPct=dvpSk&&opp?getDVPPct(teamAbbrev(opp),dvpPos,dvpSk):null;
    if(dvpPct!==null&&dvpPct<=0) return null;
    const score=scoreCMP(p,statKey,line,opp);
    if(score===null) return null;
    const avg=_favg;
    const vals=gamesCur.map(r=>r[statKey]||0);
    const hits=vals.filter(v=>v>=line).length;
    const hitRate=hits/vals.length;
    const l5=getL5Avg(p.name,statKey);
    let verdictLabel,verdictCol;
    if(score>=5.5){verdictLabel='Green Light';verdictCol='#22c55e';}
    else if(score>=3.5){verdictLabel='Lean OVER';verdictCol='#86efac';}
    else {verdictLabel='No Clear Edge';verdictCol='#888';}
    if(score<5.5) return null;
    const baseL=drLine(avg,statKey);
    return {p,opp,statKey,avg,line,l5,hitRate,hits,total:vals.length,dvpPct,score,verdictLabel,verdictCol,gamesCur:vals.length,isCustom:line!==baseL};
  }

  // generic verdict for any market (Check My Bet / player modal)
  function verdict(p, statKey, line, opp){
    const score=scoreCMP(p,statKey,line,opp);
    let label,col;
    if(score>=5.5){label='Green Light OVER';col='#22c55e';}
    else if(score>=3.5){label='Lean OVER';col='#86efac';}
    else if(score<=-3.5){label='Strong Lean UNDER';col='#ef4444';}
    else if(score<=-2){label='Lean UNDER';col='#f97316';}
    else {label='No Clear Edge';col='#888';}
    return {score,label,col};
  }

  // ---- verbose Check My Bet factor list ----
  function cmpFactors(p, statKey, line, opp, lbl){
    lbl=lbl||statKey;
    const logKey=pdToLogKey(statKey);
    const allGames=dvpByName(p.name);
    const gamesCur=_formGames(p.name);
    const logGames=allGames;
    const pdAvg=_formAvg(p.name, statKey, p);
    const aCur=gamesCur.map(r=>r[logKey]||0);
    const avg=aCur.length?aCur.reduce((x,y)=>x+y,0)/aCur.length:pdAvg;
    const l5=getL5Avg(p.name,statKey);
    const _l3g=allGames.slice(-3);
    const l3=_l3g.length>=3?_l3g.reduce((s,r)=>s+(r[logKey]||0),0)/_l3g.length:null;
    const seasonHigh=gamesCur.reduce((mx,r)=>Math.max(mx,r[logKey]||0),0);
    const hits=logGames.filter(r=>(r[logKey]||0)>=line).length;
    const hitRateRaw=logGames.length?hits/logGames.length:null;
    const hrCurRaw=gamesCur.length?gamesCur.filter(r=>(r[logKey]||0)>=line).length/gamesCur.length:null;
    const _bN=gamesCur.length, _bW=_bN/(_bN+8);
    const hitRate=hrCurRaw!==null&&hitRateRaw!==null?_bW*hrCurRaw+(1-_bW)*hitRateRaw:hitRateRaw;
    const hitRateCur=hrCurRaw;
    let cv=null;
    if(logGames.length>=5){const v=logGames.map(r=>r[logKey]||0);const mn=v.reduce((x,y)=>x+y,0)/v.length;if(mn>0)cv=Math.sqrt(v.reduce((s,x)=>s+Math.pow(x-mn,2),0)/v.length)/mn;}
    const avgGap=avg-line, l5Gap=l5!==null?l5-line:null;
    const trend=l5!==null&&avg>0?((l5-avg)/avg)*100:null;
    const dvpPos=POS_TO_DVP[getPlayerPos(p)]||getPlayerPos(p);
    const dvpSk=DVP_STAT_MAP[statKey];
    const dvpPct=opp&&dvpSk?getDVPPct(teamAbbrev(opp),dvpPos,dvpSk):null;
    const muStatKey=(getStat(statKey)||{}).a;
    const muPctVal=opp&&muStatKey?muPct(opp,muStatKey):null;
    const muI=muInfo(muPctVal);
    const vsOpp=opp?allGames.filter(r=>(r.Opp||'')===opp):[];
    const lastVs=vsOpp.slice().sort((a,b)=>{
      const ya=parseInt(a.Year||0),yb=parseInt(b.Year||0);
      if(ya!==yb)return yb-ya;
      return (b.Week||0)-(a.Week||0);
    })[0]||null;
    const lastVsVal=lastVs?(lastVs[logKey]||0):null;
    const vsOppHits=vsOpp.filter(r=>(r[logKey]||0)>=line).length;
    let dvpRank=null,dvpTotal=null;
    try{ const teams=[...new Set((DVP||[]).map(d=>d.team))];
      const ranked=teams.map(t=>({t,v:getDVPPct(teamAbbrev(t),dvpPos,dvpSk)})).filter(x=>x.v!=null).sort((a,b)=>b.v-a.v);
      dvpTotal=ranked.length; const i=ranked.findIndex(x=>teamAbbrev(x.t)===teamAbbrev(opp)); if(i>=0)dvpRank=i+1;
    }catch(e){}
    const F=[], push=(tone,text)=>F.push({tone,text});
    if(hitRate!==null){
      const pct=Math.round(hitRate*100);
      if(hitRate>=0.65) push('good',`Hit rate ${hits}/${logGames.length} (${pct}%) — highly reliable`);
      else if(hitRate>=0.5) push('lean',`Hit rate ${hits}/${logGames.length} (${pct}%) — hits more than not`);
      else if(hitRate>=0.35) push('neutral',`Hit rate ${hits}/${logGames.length} (${pct}%) — coin flip territory`);
      else push('bad',`Hit rate ${hits}/${logGames.length} (${pct}%) — rarely hits this line`);
    }
    const gp=avg>0?(avgGap/avg)*100:0;
    if(gp>15) push('good',`Season avg ${avg.toFixed(1)} — well above the line (+${gp.toFixed(0)}%)`);
    else if(gp>5) push('lean',`Season avg ${avg.toFixed(1)} — ${gp.toFixed(0)}% above the line`);
    else if(gp>-5) push('neutral',`Season avg ${avg.toFixed(1)} — right on the line (${gp>=0?'+':''}${gp.toFixed(0)}%)`);
    else if(gp>-15) push('warn',`Season avg ${avg.toFixed(1)} is ${Math.abs(gp).toFixed(0)}% below the line — against`);
    else push('bad',`Season avg ${avg.toFixed(1)} is ${Math.abs(gp).toFixed(0)}% BELOW the line — significantly against`);
    if(l3!==null&&avg>0){
      const l3p=((l3-avg)/avg)*100;
      if(l3p>15) push('hot',`L3 avg ${l3.toFixed(1)} — ${l3p.toFixed(0)}% above season avg, hot right now`);
      else if(l3p>5) push('good',`L3 avg ${l3.toFixed(1)} — trending up ${l3p.toFixed(0)}% on season avg`);
      else if(l3p<-15) push('cold',`L3 avg ${l3.toFixed(1)} — ${Math.abs(l3p).toFixed(0)}% below season avg, cold form`);
      else if(l3p<-5) push('bad',`L3 avg ${l3.toFixed(1)} — trending down ${Math.abs(l3p).toFixed(0)}% on season avg`);
    }
    if(l5!==null&&logGames.length>=5){
      if(l5Gap>0&&trend>5) push('good',`L5 avg ${l5.toFixed(1)} — trending up ${trend.toFixed(0)}% above season avg`);
      else if(l5Gap<0&&trend<-10) push('bad',`L5 avg ${l5.toFixed(1)} — trending down ${Math.abs(trend).toFixed(0)}% below season avg`);
    }
    if(hitRateCur!==null&&gamesCur.length>=5&&Math.abs(hitRateCur-(hitRate||0))>0.1){
      const hC=gamesCur.filter(r=>(r[logKey]||0)>=line).length, pC=Math.round(hitRateCur*100);
      if(hitRateCur>(hitRate||0)) push('good',`This season ${hC}/${gamesCur.length} (${pC}%) — improving on historical`);
      else push('warn',`This season ${hC}/${gamesCur.length} (${pC}%) — below historical rate`);
    }
    if(cv!==null){
      if(cv<0.22) push('good',`Exceptionally consistent — very low variance (CV ${cv.toFixed(2)})`);
      else if(cv<0.35) push('lean',`Consistent producer — normal variance (CV ${cv.toFixed(2)})`);
      else if(cv>=0.45) push('neutral',`Boom-bust player — high variance (CV ${cv.toFixed(2)}), can go big or blank`);
    }
    if(dvpPct!==null){
      const rk=dvpRank?` (#${dvpRank}/${dvpTotal} ${dvpRank<=Math.ceil((dvpTotal||32)/2)?'softest':'toughest'})`:'';
      if(dvpPct>15) push('good',`DVP: ${opp} concede ${dvpPct.toFixed(0)}% above avg to ${dvpPos}s for ${lbl}${rk} — elite soft matchup`);
      else if(dvpPct>5) push('lean',`DVP: ${opp} concede ${dvpPct.toFixed(0)}% above avg to ${dvpPos}s for ${lbl}${rk} — favourable`);
      else if(dvpPct>-5) push('neutral',`DVP: ${opp} average vs ${dvpPos}s for ${lbl}${rk} — neutral matchup`);
      else if(dvpPct>-15) push('warn',`DVP: ${opp} are tough on ${dvpPos}s for ${lbl}${rk} — negative matchup`);
      else push('bad',`DVP: ${opp} one of the toughest for ${dvpPos}s for ${lbl}${rk} — avoid`);
    }
    if(muI&&opp&&muPctVal!=null){
      if(muI.t==='Soft'||muI.t==='Fav') push('good',`Team matchup vs ${opp}: ${muI.t} (${muPctVal>=0?'+':''}${muPctVal.toFixed(0)}%)`);
      else if(muI.t==='Tough'||muI.t==='V.Tough') push('warn',`Team matchup vs ${opp}: ${muI.t} (${muPctVal>=0?'+':''}${muPctVal.toFixed(0)}%)`);
    }
    if(lastVs&&opp){
      const hit=lastVsVal>=line;
      const rl=lastVs.Week?` (W${lastVs.Week} ${lastVs.Year||''})`:'';
      const hist=vsOpp.length>1?` · ${vsOppHits}/${vsOpp.length} historical vs ${opp}`:'';
      if(hit) push('good',`Last vs ${opp}${rl}: ${lastVsVal.toFixed(1)} ${lbl} — hit the line${hist}`);
      else { const close=lastVsVal>=line*0.8; push(close?'neutral':'warn',`Last vs ${opp}${rl}: ${lastVsVal.toFixed(1)} ${lbl} — ${close?'just missed':'below the line'}${hist}`); }
    } else if(opp) push('neutral',`No previous games vs ${opp} in log`);
    const sig=opp?getContextSignals(p,statKey,opp):[];
    if(sig&&sig.length){
      sig.filter(s=>Math.abs(s.pct)>=5).sort((a,b)=>Math.abs(b.pct*b.weight)-Math.abs(a.pct*a.weight)).slice(0,5).forEach(s=>{
        const c=s.pct*s.weight, pos_=c>0, isSup=s.weight<0;
        const tone=c>15?'good':c>5?'lean':c>-5?'neutral':c>-15?'warn':'bad';
        const isEnv=s.src==='env', isOwn=s.src==='own';
        const scope=isEnv?'Game environment':isOwn?p.team:opp;
        const suf=isSup?(pos_?' — works in the bet\'s favour':' — works against the bet'):'';
        if(isEnv) push(tone,`${scope} — ${s.label}${suf}`);
        else push(tone,`${scope} — ${s.label}: ${s.pct>=0?'+':''}${s.pct.toFixed(0)}% vs league avg${suf}`);
      });
    }
    if(seasonHigh>0&&seasonHigh<line){
      if(gamesCur.length>=5) push('bad',`${CUR} season high only ${seasonHigh.toFixed(1)} — never hit this line this season`);
      else push('lean',`${CUR} season high only ${seasonHigh.toFixed(1)} — but just ${gamesCur.length} games this season`);
    } else if(seasonHigh>0) push('neutral',`${CUR} season high: ${seasonHigh.toFixed(1)}`);
    return F;
  }

  // ---- configure ----
  function configure(ctx){
    PD=(ctx.players||[]).map(aliasPlayer);
    TD=ctx.teams||[];
    TF=ctx.teamsForm||ctx.teams||[];
    DVP=ctx.dvp||[];
    _dvpIdx=ctx.logsByName||{};
    CUR=ctx.currentSeason||'2025';
    ENV=ctx.env||{};
    BA=_avgsOf(TD, false);
    TF._avgs=_avgsOf(TF, false);
    buildPDPoolAvgs();
    Object.keys(_ctxSigCache).forEach(k=>delete _ctxSigCache[k]);
    Object.keys(_l5Cache).forEach(k=>delete _l5Cache[k]);
  }

  return {
    configure, scoreCMP, cmpFactors, getContextSignals, scoreOverLine, scoreUnderLine,
    verdict, drLine, getDVPPct, muPct, muInfo, getL5Avg, getRecentAvg, getHitRate,
    POS_TO_DVP, MIN_AVG, envFor
  };
})();
