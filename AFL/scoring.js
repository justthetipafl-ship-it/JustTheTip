/* ============================================================
   scoring.js — JTT AFL scoring engine (build 2)
   ============================================================
   The irreplaceable tuning: scoreCMP + the 8-market x 7-position
   context-signals matrix + the over/under wrappers, ported VERBATIM
   from the original tool and bound to the split-data model.

   Wire-up:
     JTTScoring.configure({ players, teams, teamsForm, dvp, logsByName, currentSeason });
   then call:
     JTTScoring.scoreCMP(player, statKey, line, oppTeam)
     JTTScoring.scoreOverLine / scoreUnderLine
     JTTScoring.getContextSignals / getDVPPct / verdict

   Signals whose inputs aren't in the game-log feed (e.g. centreClear_a,
   firstPoss_a) return null and are skipped — the matrix degrades
   gracefully rather than mis-firing.
   ============================================================ */
window.JTTScoring = (function () {
  "use strict";

  // ---- module data (set by configure) ----
  let PD = [], TD = [], TF = [], DVP = [], _dvpIdx = {}, CUR = "2026";
  let BA = {};
  const _ctxSigCache = {}, _l5Cache = {}, _pdPoolAvgs = {};

  // ---- constants ----
  const POS_TO_DVP = {
    'Midfielder':'Midfielder','Mid-Forward':'Mid-Forward','Gen. Forward':'Gen. Forward',
    'Key Forward':'Key Forward','Gen. Defender':'Gen. Defender','Key Defender':'Key Defender','Ruck':'Ruck'
  };
  // statKey -> DVP table field (identity here — DVP rows use internal keys)
  const DVP_STAT_MAP = {
    disposals:'disposals',kicks:'kicks',handballs:'handballs',marks:'marks',tackles:'tackles',
    hitouts:'hitouts',goals:'goals',behinds:'behinds',clearances:'clearances',inside50s:'inside50s',
    intercepts:'intercepts',contested:'contested',dreamteam:'dreamteam',supercoach:'supercoach',
    shotsAtGoal:'shotsAtGoal'
  };
  // stat -> its "_a" (allowed) team key, used for muPct in scoreCMP
  const ALL = [
    {k:'disposals',a:'disposals_a'},{k:'kicks',a:'kicks_a'},{k:'handballs',a:'handballs_a'},
    {k:'marks',a:'marks_a'},{k:'tackles',a:'tackles_a'},{k:'goals',a:'goals_a'},
    {k:'behinds',a:'behinds_a'},{k:'shotsAtGoal',a:'shotsAtGoal_a'},{k:'clearances',a:'clearances_a'},
    {k:'inside50s',a:'inside50s_a'},{k:'contested',a:'contested_a'},{k:'intercepts',a:'intercepts_a'},
    {k:'hitouts',a:'hitouts_a'},{k:'dreamteam',a:'dreamteam_a'},{k:'supercoach',a:'supercoach_a'},
    {k:'groundBallGets',a:'groundBallGets_a'},{k:'metresGained',a:'metresGained_a'},
    {k:'xScore',a:'xScore_a'}
  ];
  function getStat(k){ return ALL.find(s=>s.k===k); }

  // ---- accessors (bound to split-data model) ----
  function dvpByName(name){ return _dvpIdx[name] || []; }
  function isCurSeason(r){ return String(r.Year) === CUR; }
  function getPlayerPos(p){ return p.position; }
  function teamAbbrev(t){ return t; }   // data uses full names throughout
  function teamFull(t){ return t; }
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

  // ---- team allowed/form percentages ----
  function muPct(opp, akey){
    if(!akey) return null;
    const td = TD.find(t=>t.team===opp); if(!td) return null;
    const v = td[akey], avg = BA[akey];
    return avg ? ((v-avg)/avg)*100 : null;
  }
  function tfPctFor(opp, tfKey){
    if(!TF || !TF.length || !TF._avgs) return null;
    const t = TF.find(x=>x.team===opp); if(!t) return null;
    const avg = TF._avgs[tfKey];
    if(!avg || t[tfKey]==null) return null;
    return ((t[tfKey]-avg)/avg)*100;
  }
  function tdPctFor(opp, tdKey){ return muPct(opp, tdKey); }

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
  function getHitRate(name, statKey, line){
    const games = dvpByName(name); if(games.length<3) return null;
    const hits = games.filter(g=>(g[statKey]||0)>=line).length;
    return {rate:hits/games.length, n:games.length};
  }

  // ---- position-pool averages (affinity), only for keys we actually carry ----
  function buildPDPoolAvgs(){
    Object.keys(_pdPoolAvgs).forEach(k=>delete _pdPoolAvgs[k]);
    const groups = {};
    PD.forEach(p=>{ const pg=POS_TO_DVP[p.position]||p.position||'Unknown'; (groups[pg]=groups[pg]||[]).push(p); });
    // matrix poolKey -> player field that holds it
    const POOL = {cbaPct:'cba', clearances:'clearances', contestedPoss:'contested',
      intercepts:'intercepts', interceptMarks:'interceptMarks', tackles:'tackles', pressureActs:'pressureActs'};
    Object.entries(groups).forEach(([pg, players])=>{
      _pdPoolAvgs[pg]={};
      Object.entries(POOL).forEach(([poolKey, field])=>{
        const vals=players.map(p=>+(p[field]||0)).filter(v=>v>0);
        if(vals.length) _pdPoolAvgs[pg][poolKey]=vals.reduce((a,b)=>a+b,0)/vals.length;
      });
    });
  }
  // add the aliases the matrix's inline `p.cbaPct||p.CentreBounceAttendancePercentage` reads expect
  function aliasPlayer(p){
    p.cbaPct=p.cba; p.CentreBounceAttendancePercentage=p.cba;
    p.TotalClearances=p.clearances; p.contestedPoss=p.contested; p.ContestedPossessions=p.contested;
    p.InterceptMarks=p.interceptMarks; p.Intercepts=p.intercepts;
    p.Tackles=p.tackles; p.PressureActs=p.pressureActs;
    return p;
  }

  function _avgsOf(rows){
    const a={}; if(!rows||!rows.length) return a;
    const keys = Object.keys(rows[0]).filter(k=>k.endsWith('_a'));
    keys.forEach(k=>{ const v=rows.map(r=>r[k]).filter(x=>x!=null); if(v.length)a[k]=v.reduce((s,x)=>s+x,0)/v.length; });
    return a;
  }

  /* ============== CONTEXT SIGNALS MATRIX (verbatim) ============== */
  function getContextSignals(p, statType, opp){
    const cacheKey=(p.name||'')+'|'+(statType||'')+'|'+(opp||'');
    if(_ctxSigCache[cacheKey]) return _ctxSigCache[cacheKey];
    const _r=_getContextSignalsInner(p, statType, opp);
    _ctxSigCache[cacheKey]=_r; return _r;
  }
  function _getContextSignalsInner(p, statType, opp){
    const pos=p.position||'';
    const isFwd=pos==='Key Forward'||pos==='Gen. Forward'||pos==='Mid-Forward';
    const isMid=pos==='Midfielder'||pos==='Mid-Forward';
    const isDef=pos==='Key Defender'||pos==='Gen. Defender';
    const isRuck=pos==='Ruck';
    const signals=[];
    function add(label,tdKey,tfKey,weight){
      const td=tdKey?tdPctFor(opp,tdKey):null;
      const tf=tfKey?tfPctFor(opp,tfKey):null;
      const pct=tf!==null?tf:td;
      if(pct!==null) signals.push({label,pct,weight,src:tf!==null?'form':'season'});
    }
    function addI(label,tdKey,tfKey,weight,playerVal,poolKey){
      const td=tdKey?tdPctFor(opp,tdKey):null;
      const tf=tfKey?tfPctFor(opp,tfKey):null;
      const oppPct=tf!==null?tf:td;
      if(oppPct===null) return;
      const pg=POS_TO_DVP[p.position]||p.position||'';
      const pool=_pdPoolAvgs[pg]||{};
      const poolAvg=pool[poolKey]||pool[playerVal]||null;
      const pVal=typeof playerVal==='number'?playerVal:(+(p[playerVal]||p[poolKey]||0));
      let affinity=1.0;
      if(poolAvg&&poolAvg>0){ const rel=(pVal-poolAvg)/poolAvg; affinity=Math.max(0.5,Math.min(1.6,1+rel)); }
      const adjPct=oppPct*affinity;
      const tag=affinity>1.25?' (strength match)':affinity<0.75?' (poor fit)':'';
      signals.push({label:label+tag,pct:adjPct,weight,src:tf!==null?'form+player':'season+player'});
    }

    if(statType==='disposals'){
      if(pos==='Midfielder'){
        addI('Ctr Clr Allowed','centreClear_a',null,1.3,p.cbaPct||p.CentreBounceAttendancePercentage||0,'cbaPct');
        addI('Stp Clr Allowed','stopClear_a',null,1.2,p.clearances||p.TotalClearances||0,'clearances');
        add('1st Poss Allowed','firstPoss_a','firstPoss_a',1.1);
        add('HO Adv Allowed','hitoutsAdv_a','hitoutsAdv_a',0.7);
        add('Post Cl GBG','postClearGBG_a','postClearGBG_a',0.6);
        addI('Cont Poss Allowed','contested_a','contested_a',0.6,p.contestedPoss||p.ContestedPossessions||0,'contestedPoss');
        add('Hard BG Allowed','hardBallGets_a',null,0.6);
        add('Opp Clangers','clangers_a',null,0.3);
        add('Pts From CB','pointsFromCB_a','pointsFromCB_a',0.2);
        add('Pts From Stop','pointsFromStop_a','pointsFromStop_a',0.2);
        add('Opp Def Pressure','pressureActsDefHalf_a','pressureActsDefHalf_a',-0.7);
      }
      if(pos==='Mid-Forward'){
        add('I50 Allowed','inside50s_a','inside50s_a',1.1);
        add('F50 GBG Allowed','gbgF50_a','gbgF50_a',1.0);
        add('Ctr Clr Allowed','centreClear_a',null,0.7);
        add('1st Poss Allowed','firstPoss_a','firstPoss_a',0.6);
        add('Cont Poss Allowed','contested_a','contested_a',0.5);
        add('Post Cl GBG','postClearGBG_a','postClearGBG_a',0.5);
        add('Opp Clangers','clangers_a',null,0.3);
        add('xChain Allowed','xChainScore_a','xChainScore_a',0.2);
        add('Opp Def Pressure','pressureActsDefHalf_a','pressureActsDefHalf_a',-0.6);
      }
      if(pos==='Key Forward'){
        add('I50 Allowed','inside50s_a','inside50s_a',1.2);
        add('F50 GBG Allowed','gbgF50_a','gbgF50_a',1.0);
        add('MoL Allowed','marksOnLead_a',null,0.7);
        add('xChain Allowed','xChainScore_a','xChainScore_a',0.2);
        add('Opp Def Pressure','pressureActsDefHalf_a','pressureActsDefHalf_a',-0.8);
      }
      if(pos==='Gen. Forward'){
        add('I50 Allowed','inside50s_a','inside50s_a',1.2);
        add('F50 GBG Allowed','gbgF50_a','gbgF50_a',1.1);
        add('xChain Allowed','xChainScore_a','xChainScore_a',0.2);
        add('Opp Def Pressure','pressureActsDefHalf_a','pressureActsDefHalf_a',-0.7);
      }
      if(pos==='Key Defender'){
        add('Kick-Ins Allowed','kickIns_a','kickIns_a',1.3);
        addI('I50 Allowed','inside50s_a','inside50s_a',1.1,p.interceptMarks||p.InterceptMarks||0,'interceptMarks');
        add('R50 Allowed','rebound50s_a','rebound50s_a',1.0);
        addI('Intercepts Allowed','intercepts_a','intercepts_a',1.0,p.intercepts||p.Intercepts||0,'intercepts');
        add('D50→F50 Allowed','d50toF50_a',null,0.6);
        add('Opp Clangers','clangers_a',null,0.3);
        add('Pts Def Half','pointsFromDefHalf_a','pointsFromDefHalf_a',0.2);
      }
      if(pos==='Gen. Defender'){
        add('I50 Allowed','inside50s_a','inside50s_a',1.1);
        add('R50 Allowed','rebound50s_a','rebound50s_a',1.0);
        addI('Intercepts Allowed','intercepts_a','intercepts_a',0.9,p.intercepts||p.Intercepts||0,'intercepts');
        addI('Kick-Ins Allowed','kickIns_a','kickIns_a',0.8,p.kickIns||p.KickIns||0,'kickIns');
        add('Cont Poss Allowed','contested_a','contested_a',0.5);
        add('D50→F50 Allowed','d50toF50_a',null,0.5);
        add('Hard BG Allowed','hardBallGets_a',null,0.4);
        add('Opp Clangers','clangers_a',null,0.3);
      }
      if(isRuck){
        add('HO Adv Allowed','hitoutsAdv_a','hitoutsAdv_a',1.3);
        add('1st Poss Allowed','firstPoss_a','firstPoss_a',1.0);
        add('Ctr Clr Allowed','centreClear_a',null,0.7);
        add('Cont Poss Allowed','contested_a','contested_a',0.5);
      }
    }

    if(statType==='kicks'){
      if(pos==='Midfielder'){
        add('Ctr Clr Allowed','centreClear_a',null,1.2);
        add('D50→F50 Allowed','d50toF50_a',null,1.1);
        add('1st Poss Allowed','firstPoss_a','firstPoss_a',0.8);
        add('xChain Allowed','xChainScore_a','xChainScore_a',0.6);
        add('Metres Allowed','metresGained_a',null,0.2);
        add('Opp Def Pressure','pressureActsDefHalf_a','pressureActsDefHalf_a',-0.7);
      }
      if(pos==='Mid-Forward'){
        add('I50 Allowed','inside50s_a','inside50s_a',1.1);
        add('F50 GBG Allowed','gbgF50_a','gbgF50_a',0.9);
        add('Ctr Clr Allowed','centreClear_a',null,0.6);
        add('xChain Allowed','xChainScore_a','xChainScore_a',0.5);
        add('D50→F50 Allowed','d50toF50_a',null,0.5);
      }
      if(pos==='Key Forward'){
        add('I50 Allowed','inside50s_a','inside50s_a',1.1);
        add('F50 GBG Allowed','gbgF50_a','gbgF50_a',0.9);
        add('xChain Allowed','xChainScore_a','xChainScore_a',0.5);
        add('Opp Def Pressure','pressureActsDefHalf_a','pressureActsDefHalf_a',-0.8);
      }
      if(pos==='Gen. Forward'){
        add('I50 Allowed','inside50s_a','inside50s_a',1.1);
        add('F50 GBG Allowed','gbgF50_a','gbgF50_a',0.9);
        add('xChain Allowed','xChainScore_a','xChainScore_a',0.5);
        add('Opp Def Pressure','pressureActsDefHalf_a','pressureActsDefHalf_a',-0.7);
      }
      if(pos==='Key Defender'){
        add('Kick-Ins Allowed','kickIns_a','kickIns_a',1.4);
        add('R50 Allowed','rebound50s_a','rebound50s_a',1.2);
        add('I50 Allowed','inside50s_a','inside50s_a',1.1);
        add('D50→F50 Allowed','d50toF50_a',null,1.0);
        add('Intercepts Allowed','intercepts_a','intercepts_a',0.8);
        add('Metres Allowed','metresGained_a',null,0.2);
      }
      if(pos==='Gen. Defender'){
        add('R50 Allowed','rebound50s_a','rebound50s_a',1.2);
        add('I50 Allowed','inside50s_a','inside50s_a',1.1);
        add('D50→F50 Allowed','d50toF50_a',null,1.0);
        add('Kick-Ins Allowed','kickIns_a','kickIns_a',0.7);
        add('Intercepts Allowed','intercepts_a','intercepts_a',0.7);
        add('Metres Allowed','metresGained_a',null,0.2);
      }
      if(isRuck){
        add('HO Adv Allowed','hitoutsAdv_a','hitoutsAdv_a',1.2);
        add('1st Poss Allowed','firstPoss_a','firstPoss_a',0.7);
        add('D50→F50 Allowed','d50toF50_a',null,0.5);
        add('Ctr Clr Allowed','centreClear_a',null,0.6);
      }
    }

    if(statType==='handballs'){
      if(pos==='Midfielder'){
        add('Cont Poss Allowed','contested_a','contested_a',1.2);
        add('Hard BG Allowed','hardBallGets_a',null,1.1);
        add('GBG Allowed','groundBallGets_a','groundBallGets_a',0.7);
        add('Post Cl CP Allowed','postClearCont_a','postClearCont_a',0.7);
        add('Post Cl GBG','postClearGBG_a','postClearGBG_a',0.6);
      }
      if(pos==='Mid-Forward'){
        add('Cont Poss Allowed','contested_a','contested_a',1.1);
        add('Hard BG Allowed','hardBallGets_a',null,1.0);
        add('F50 GBG Allowed','gbgF50_a','gbgF50_a',0.7);
        add('GBG Allowed','groundBallGets_a','groundBallGets_a',0.6);
        add('Post Cl CP Allowed','postClearCont_a','postClearCont_a',0.6);
      }
      if(pos==='Key Forward'){ signals.push({label:'Low HB position',pct:-12,weight:1.0,src:'position'}); }
      if(pos==='Gen. Forward'){
        add('F50 GBG Allowed','gbgF50_a','gbgF50_a',1.2);
        add('Cont Poss Allowed','contested_a','contested_a',0.7);
        add('Hard BG Allowed','hardBallGets_a',null,0.6);
        add('Post Cl CP Allowed','postClearCont_a','postClearCont_a',0.5);
      }
      if(pos==='Key Defender'){
        add('I50 Allowed','inside50s_a','inside50s_a',0.8);
        add('Cont Poss Allowed','contested_a','contested_a',0.6);
        add('Hard BG Allowed','hardBallGets_a',null,0.5);
      }
      if(pos==='Gen. Defender'){
        add('Cont Poss Allowed','contested_a','contested_a',0.7);
        add('I50 Allowed','inside50s_a','inside50s_a',0.6);
        add('Hard BG Allowed','hardBallGets_a',null,0.6);
        add('GBG Allowed','groundBallGets_a','groundBallGets_a',0.5);
      }
      if(isRuck){
        add('HO Adv Allowed','hitoutsAdv_a','hitoutsAdv_a',1.1);
        add('Cont Poss Allowed','contested_a','contested_a',0.8);
      }
    }

    if(statType==='marks'){
      if(pos==='Key Forward'){
        addI('MI50 Allowed','marksInside50_a',null,1.6,p.marksInside50||p.MarksInside50||0,'marksInside50');
        addI('MoL Allowed','marksOnLead_a',null,1.5,p.marksOnLead||p.MarksOnLead||0,'marksOnLead');
        add('I50 Allowed','inside50s_a','inside50s_a',0.8);
        add('xChain Allowed','xChainScore_a','xChainScore_a',0.2);
        add('Spoils Allowed','spoils_a',null,-0.7);
        add('Opp Def Pressure','pressureActsDefHalf_a','pressureActsDefHalf_a',-0.7);
        if(p.i50TargetRank) signals.push({label:'#'+p.i50TargetRank+' I50 Target',pct:Math.round((11-p.i50TargetRank)*4),weight:1.4,src:'targeting'});
        else if((p.i50TargetsLastGame||0)>=4) signals.push({label:p.i50TargetsLastGame+' I50 tgt LG',pct:15,weight:1.0,src:'targeting'});
      }
      if(pos==='Gen. Forward'){
        add('MI50 Allowed','marksInside50_a',null,1.4);
        add('I50 Allowed','inside50s_a','inside50s_a',0.7);
        add('Spoils Allowed','spoils_a',null,-0.6);
        add('Opp Def Pressure','pressureActsDefHalf_a','pressureActsDefHalf_a',-0.6);
      }
      if(pos==='Mid-Forward'){
        add('MI50 Allowed','marksInside50_a',null,1.3);
        add('MoL Allowed','marksOnLead_a',null,0.7);
        add('I50 Allowed','inside50s_a','inside50s_a',0.6);
        add('Spoils Allowed','spoils_a',null,-0.5);
      }
      if(pos==='Key Defender'){
        add('Intercepts Allowed','intercepts_a','intercepts_a',1.5);
        add('R50 Allowed','rebound50s_a','rebound50s_a',0.8);
        add('I50 Allowed','inside50s_a','inside50s_a',0.7);
        add('Kick-Ins Allowed','kickIns_a','kickIns_a',0.5);
        add('Opp Clangers','clangers_a',null,0.3);
      }
      if(pos==='Gen. Defender'){
        add('Intercepts Allowed','intercepts_a','intercepts_a',1.4);
        add('I50 Allowed','inside50s_a','inside50s_a',0.7);
        add('R50 Allowed','rebound50s_a','rebound50s_a',0.6);
        add('Kick-Ins Allowed','kickIns_a','kickIns_a',0.4);
        add('Opp Clangers','clangers_a',null,0.3);
      }
      if(pos==='Midfielder'){
        add('MI50 Allowed','marksInside50_a',null,0.4);
        add('Spoils Allowed','spoils_a',null,-0.5);
      }
      if(isRuck){
        add('HO Adv Allowed','hitoutsAdv_a','hitoutsAdv_a',1.1);
        add('Spoils Allowed','spoils_a',null,-0.4);
      }
    }

    if(statType==='tackles'){
      if(pos==='Midfielder'){
        addI('Cont Poss',null,'contested',1.4,p.tackles||p.Tackles||0,'tackles');
        add('Hard BG',null,'hardBallGets',1.3);
        addI('GBG',null,'groundBallGets',0.8,p.pressureActs||p.PressureActs||0,'pressureActs');
      }
      if(pos==='Mid-Forward'){
        addI('Cont Poss',null,'contested',1.3,p.tackles||p.Tackles||0,'tackles');
        add('Hard BG',null,'hardBallGets',1.2);
        add('GBG',null,'groundBallGets',0.7);
      }
      if(pos==='Gen. Forward'){
        add('Hard BG',null,'hardBallGets',1.3);
        add('Cont Poss',null,'contested',0.8);
        add('GBG',null,'groundBallGets',0.6);
        signals.push({label:'Lower tackle pos',pct:-5,weight:1.0,src:'position'});
      }
      if(pos==='Key Forward'){ signals.push({label:'Low tackle position',pct:-8,weight:1.0,src:'position'}); }
      if(pos==='Key Defender'||pos==='Gen. Defender'){
        add('Opp I50',null,'inside50s',1.3);
        add('Cont Poss',null,'contested',0.8);
        add('Hard BG',null,'hardBallGets',0.7);
        add('GBG',null,'groundBallGets',0.6);
      }
      if(isRuck){
        add('Cont Poss',null,'contested',1.2);
        add('Hard BG',null,'hardBallGets',0.8);
        add('GBG',null,'groundBallGets',0.5);
      }
    }

    if(statType==='clearances'){
      if(pos==='Midfielder'){
        addI('Ctr Clr Allowed','centreClear_a',null,1.5,p.cbaPct||p.CentreBounceAttendancePercentage||0,'cbaPct');
        addI('Stp Clr Allowed','stopClear_a',null,1.4,p.clearances||p.TotalClearances||0,'clearances');
        add('1st Poss Allowed','firstPoss_a','firstPoss_a',1.1);
        add('HO Adv Allowed','hitoutsAdv_a','hitoutsAdv_a',0.8);
        add('Post Cl GBG','postClearGBG_a','postClearGBG_a',0.6);
        add('Hard BG Allowed','hardBallGets_a',null,0.6);
        add('Pts From CB','pointsFromCB_a','pointsFromCB_a',0.2);
        add('Pts From Stop','pointsFromStop_a','pointsFromStop_a',0.2);
      }
      if(pos==='Mid-Forward'){
        add('Ctr Clr Allowed','centreClear_a',null,0.8);
        add('Stp Clr Allowed','stopClear_a',null,1.0);
        add('1st Poss Allowed','firstPoss_a','firstPoss_a',0.7);
        add('HO Adv Allowed','hitoutsAdv_a','hitoutsAdv_a',0.6);
        add('Post Cl GBG','postClearGBG_a','postClearGBG_a',0.5);
      }
      if(isDef||(isFwd&&pos!=='Mid-Forward')){
        signals.push({label:'Poor position for clears',pct:-20,weight:1.0,src:'position'});
      }
      if(isRuck){
        addI('HO Adv Allowed','hitoutsAdv_a','hitoutsAdv_a',1.6,p.hitoutsWinPct||p.HitoutsWinPercentage||0,'hitoutsWinPct');
        add('Stp Clr Allowed','stopClear_a',null,1.4);
        add('Ctr Clr Allowed','centreClear_a',null,1.2);
        add('1st Poss Allowed','firstPoss_a','firstPoss_a',0.6);
      }
    }

    if(statType==='goals'||statType==='behinds'){
      if(pos==='Key Forward'){
        add('Goals Allowed','goals_a','goals_a',1.5);
        add('xScore Allowed','xScore_a','xScore_a',1.4);
        add('Shots Allowed','shotsAtGoal_a','shotsAtGoal_a',1.3);
        addI('MI50 Allowed','marksInside50_a',null,1.2,p.marksInside50||p.MarksInside50||0,'marksInside50');
        addI('MoL Allowed','marksOnLead_a',null,1.1,p.marksOnLead||p.MarksOnLead||0,'marksOnLead');
        add('I50 Allowed','inside50s_a','inside50s_a',0.7);
        add('xScore/Shot','xScorePerShot_a','xScorePerShot_a',0.7);
        add('F50 GBG Allowed','gbgF50_a','gbgF50_a',0.2);
        add('Opp Tack I50','tacklesInside50_a',null,-0.8);
        if(p.i50TargetRank) signals.push({label:'#'+p.i50TargetRank+' I50 Target',pct:Math.round((11-p.i50TargetRank)*5),weight:1.5,src:'targeting'});
        else if((p.i50TargetsLastGame||0)>=4) signals.push({label:p.i50TargetsLastGame+' I50 tgt LG',pct:12,weight:1.1,src:'targeting'});
      }
      if(pos==='Gen. Forward'){
        add('Goals Allowed','goals_a','goals_a',1.5);
        add('xScore Allowed','xScore_a','xScore_a',1.4);
        add('Shots Allowed','shotsAtGoal_a','shotsAtGoal_a',1.3);
        add('F50 GBG Allowed','gbgF50_a','gbgF50_a',1.1);
        add('I50 Allowed','inside50s_a','inside50s_a',0.9);
        add('xScore/Shot','xScorePerShot_a','xScorePerShot_a',0.7);
        add('MI50 Allowed','marksInside50_a',null,0.6);
        add('xChain Allowed','xChainScore_a','xChainScore_a',0.2);
        add('Opp Tack I50','tacklesInside50_a',null,-0.7);
        if(p.i50TargetRank) signals.push({label:'#'+p.i50TargetRank+' I50 Target',pct:Math.round((11-p.i50TargetRank)*3),weight:1.2,src:'targeting'});
        else if((p.i50TargetsLastGame||0)>=3) signals.push({label:p.i50TargetsLastGame+' I50 tgt LG',pct:10,weight:0.9,src:'targeting'});
      }
      if(pos==='Mid-Forward'){
        add('Goals Allowed','goals_a','goals_a',1.5);
        add('xScore Allowed','xScore_a','xScore_a',1.4);
        add('Shots Allowed','shotsAtGoal_a','shotsAtGoal_a',1.2);
        add('I50 Allowed','inside50s_a','inside50s_a',1.0);
        add('F50 GBG Allowed','gbgF50_a','gbgF50_a',0.9);
        add('Ctr Clr Allowed','centreClear_a',null,0.7);
        add('xScore/Shot','xScorePerShot_a','xScorePerShot_a',0.6);
        add('xChain Allowed','xChainScore_a','xChainScore_a',0.2);
        add('Opp Tack I50','tacklesInside50_a',null,-0.6);
        if(p.i50TargetRank) signals.push({label:'#'+p.i50TargetRank+' I50 Target',pct:Math.round((11-p.i50TargetRank)*2),weight:1.0,src:'targeting'});
        else if((p.i50TargetsLastGame||0)>=3) signals.push({label:p.i50TargetsLastGame+' I50 tgt LG',pct:8,weight:0.8,src:'targeting'});
      }
      if(pos==='Midfielder'){
        add('Goals Allowed','goals_a','goals_a',1.4);
        add('xScore Allowed','xScore_a','xScore_a',1.2);
        add('I50 Allowed','inside50s_a','inside50s_a',1.0);
        add('F50 GBG Allowed','gbgF50_a','gbgF50_a',0.7);
        add('Ctr Clr Allowed','centreClear_a',null,0.6);
        add('xScore/Shot','xScorePerShot_a','xScorePerShot_a',0.6);
        add('Shots Allowed','shotsAtGoal_a','shotsAtGoal_a',0.2);
        add('Opp Tack I50','tacklesInside50_a',null,-0.5);
      }
      if(isDef||isRuck){
        add('Goals Allowed','goals_a','goals_a',0.8);
        add('xScore Allowed','xScore_a','xScore_a',0.2);
      }
    }

    if(statType==='dreamteam'||statType==='supercoach'){
      if(pos==='Midfielder'){
        add('Ctr Clr Allowed','centreClear_a',null,1.2);
        add('1st Poss Allowed','firstPoss_a','firstPoss_a',1.0);
        add('Cont Poss Allowed','contested_a','contested_a',0.6);
        add('Post Cl GBG','postClearGBG_a','postClearGBG_a',0.5);
        add('HO Adv Allowed','hitoutsAdv_a','hitoutsAdv_a',0.5);
        add('Hard BG Allowed','hardBallGets_a',null,0.5);
        add('Pts From CB','pointsFromCB_a','pointsFromCB_a',0.2);
      }
      if(pos==='Mid-Forward'){
        add('Goals Allowed','goals_a','goals_a',1.1);
        add('I50 Allowed','inside50s_a','inside50s_a',0.9);
        add('F50 GBG Allowed','gbgF50_a','gbgF50_a',0.8);
        add('Ctr Clr Allowed','centreClear_a',null,0.6);
        add('xScore Allowed','xScore_a','xScore_a',0.2);
        add('Cont Poss Allowed','contested_a','contested_a',0.2);
      }
      if(pos==='Key Forward'){
        add('Goals Allowed','goals_a','goals_a',1.3);
        add('xScore Allowed','xScore_a','xScore_a',1.0);
        add('MI50 Allowed','marksInside50_a',null,1.0);
        add('Shots Allowed','shotsAtGoal_a','shotsAtGoal_a',0.7);
        add('I50 Allowed','inside50s_a','inside50s_a',0.2);
      }
      if(pos==='Gen. Forward'){
        add('Goals Allowed','goals_a','goals_a',1.1);
        add('xScore Allowed','xScore_a','xScore_a',0.9);
        add('F50 GBG Allowed','gbgF50_a','gbgF50_a',0.7);
        add('I50 Allowed','inside50s_a','inside50s_a',0.6);
        add('Shots Allowed','shotsAtGoal_a','shotsAtGoal_a',0.2);
      }
      if(pos==='Key Defender'){
        add('Intercepts Allowed','intercepts_a','intercepts_a',1.2);
        add('R50 Allowed','rebound50s_a','rebound50s_a',1.0);
        add('Kick-Ins Allowed','kickIns_a','kickIns_a',0.9);
        add('I50 Allowed','inside50s_a','inside50s_a',0.6);
        add('D50→F50 Allowed','d50toF50_a',null,0.2);
      }
      if(pos==='Gen. Defender'){
        add('Intercepts Allowed','intercepts_a','intercepts_a',1.1);
        add('R50 Allowed','rebound50s_a','rebound50s_a',1.0);
        add('I50 Allowed','inside50s_a','inside50s_a',0.7);
        add('Kick-Ins Allowed','kickIns_a','kickIns_a',0.5);
        add('D50→F50 Allowed','d50toF50_a',null,0.2);
        add('Cont Poss Allowed','contested_a','contested_a',0.2);
      }
      if(isRuck){
        add('HO Allowed','hitouts_a',null,1.3);
        add('HO Adv Allowed','hitoutsAdv_a','hitoutsAdv_a',1.2);
        add('Ctr Clr Allowed','centreClear_a',null,0.7);
        add('1st Poss Allowed','firstPoss_a','firstPoss_a',0.6);
      }
    }

    return signals;
  }

  /* ============== ENGINE (verbatim) ============== */
  function drLine(avg){ if(avg<15) return null; return Math.round(avg); }

  function scoreCMP(p, statKey, line, opp){
    const logKey=pdToLogKey(statKey);
    const allGames=dvpByName(p.name);
    const games2026=allGames.filter(r=>isCurSeason(r));
    const logGames=allGames;
    const pdAvg=statKey==='dreamteam'?p.dreamteam:(p[statKey]||0);
    const avg2026vals=games2026.map(r=>r[logKey]||0);
    const avg=avg2026vals.length?avg2026vals.reduce((a,b)=>a+b,0)/avg2026vals.length:pdAvg;
    const l5=getL5Avg(p.name,statKey);
    const l10=getRecentAvg(p.name,statKey,10);
    const _l3g=allGames.slice(-3);
    const l3=_l3g.length>=3?_l3g.reduce((s,r)=>s+(r[logKey]||0),0)/_l3g.length:null;
    const seasonHigh=games2026.reduce((mx,r)=>Math.max(mx,r[logKey]||0),0);
    const hits=logGames.filter(r=>(r[logKey]||0)>=line).length;
    const hitRateRaw=logGames.length?hits/logGames.length:null;
    const hitRate2026Raw=games2026.length?games2026.filter(r=>(r[logKey]||0)>=line).length/games2026.length:null;
    const _blendN=games2026.length;
    const _blendW=_blendN/(_blendN+8);
    const hitRate=hitRate2026Raw!==null&&hitRateRaw!==null?_blendW*hitRate2026Raw+(1-_blendW)*hitRateRaw:hitRateRaw;
    const hitRate2026=hitRate2026Raw;
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
    const vsOppGames=opp?allGames.filter(r=>{const ro=r.opponent||'';return ro===opp;}):[];
    const lastVsOpp=vsOppGames.sort((a,b)=>{const ya=parseInt(a.year||a.Year||0),yb=parseInt(b.year||b.Year||0);if(ya!==yb)return yb-ya;const rn=rd=>{const m=(rd||'').match(/\d+/);return m?parseInt(m[0]):0;};return rn(b.round||b.RoundName)-rn(a.round||a.RoundName);})[0]||null;
    const lastVsOppVal=lastVsOpp?(lastVsOpp[logKey]||0):null;
    const allSignalsRaw=opp?getContextSignals(p,statKey,opp):[];
    const SCORING_STATS=new Set(['goals','behinds','dreamteam','supercoach','scoreInv','inside50s']);
    const POINTS_LABELS=new Set(['Pts Fwd Half','Pts Def Half','Pts From CB','Pts From Stop','Pts From Turn','Ctr Bnc','xScore/Shot']);
    const allSignals=allSignalsRaw.filter(s=>SCORING_STATS.has(statKey)||!POINTS_LABELS.has(s.label));

    let score=0;
    if(hitRate!==null){
      if(hitRate>=0.65) score+=3;
      else if(hitRate>=0.5) score+=1.5;
      else if(hitRate<0.35) score-=2.5;
    }
    if(avgGap>5) score+=1;
    else if(avgGap>1) score+=0.3;
    else if(avgGap<=-1) score-=2;
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
    if(hitRate2026!==null&&games2026.length>=5&&Math.abs(hitRate2026-(hitRate||0))>0.1){
      if(hitRate2026>(hitRate||0)) score+=0.5; else score-=0.5;
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
    if(seasonHigh>0&&seasonHigh<line&&games2026.length>=5) score-=4;
    return score;
  }

  // ---- over / under wrappers (from context dump) ----
  function scoreUnderLine(p, opp, line){
    if(!line || (p.disposals||0)<10) return null;
    const games2026=dvpByName(p.name).filter(isCurSeason);
    if(games2026.length<3) return null;
    const dvpPos=POS_TO_DVP[getPlayerPos(p)]||getPlayerPos(p);
    const dvpSk=DVP_STAT_MAP['disposals'];
    const dvpPct=dvpSk&&opp?getDVPPct(teamAbbrev(opp),dvpPos,dvpSk):null;
    if(dvpPct!==null&&dvpPct>=0) return null;
    const score=scoreCMP(p,'disposals',line,opp);
    const avg=p.disposals||0;
    const vals=games2026.map(r=>r.disposals||0);
    const hits=vals.filter(v=>v>=line).length;
    const hitRate=hits/vals.length;
    const l5=getL5Avg(p.name,'disposals');
    let verdictLabel,verdictCol;
    if(score<=-3.5){verdictLabel='Strong Lean UNDER';verdictCol='#ef4444';}
    else if(score<=-2){verdictLabel='Lean UNDER';verdictCol='#f97316';}
    else {verdictLabel='No Clear Edge';verdictCol='#888';}
    if(score>-3.5) return null;
    const baseL=drLine(avg);
    return {p,opp,avg,line,l5,hitRate,hits,total:vals.length,dvpPct,score,verdictLabel,verdictCol,games2026:vals.length,isCustom:line!==baseL};
  }
  function scoreOverLine(p, opp, line){
    if(!line || (p.disposals||0)<15) return null;
    const games2026=dvpByName(p.name).filter(isCurSeason);
    if(games2026.length<3) return null;
    const dvpPos=POS_TO_DVP[getPlayerPos(p)]||getPlayerPos(p);
    const dvpSk=DVP_STAT_MAP['disposals'];
    const dvpPct=dvpSk&&opp?getDVPPct(teamAbbrev(opp),dvpPos,dvpSk):null;
    if(dvpPct!==null&&dvpPct<=0) return null;
    const score=scoreCMP(p,'disposals',line,opp);
    if(score===null) return null;
    const avg=p.disposals||0;
    const vals=games2026.map(r=>r.disposals||0);
    const hits=vals.filter(v=>v>=line).length;
    const hitRate=hits/vals.length;
    const l5=getL5Avg(p.name,'disposals');
    let verdictLabel,verdictCol;
    if(score>=5.5){verdictLabel='Green Light';verdictCol='#22c55e';}
    else if(score>=3.5){verdictLabel='Lean OVER';verdictCol='#86efac';}
    else {verdictLabel='No Clear Edge';verdictCol='#888';}
    if(score<5.5) return null;
    const baseL=drLine(avg);
    return {p,opp,avg,line,l5,hitRate,hits,total:vals.length,dvpPct,score,verdictLabel,verdictCol,games2026:vals.length,isCustom:line!==baseL};
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

  // ---- configure ----
  function configure(ctx){
    PD=(ctx.players||[]).map(aliasPlayer);
    TD=ctx.teams||[];
    TF=ctx.teamsForm||ctx.teams||[];
    DVP=ctx.dvp||[];
    _dvpIdx=ctx.logsByName||{};
    CUR=ctx.currentSeason||'2026';
    BA=_avgsOf(TD);
    TF._avgs=_avgsOf(TF);
    buildPDPoolAvgs();
    Object.keys(_ctxSigCache).forEach(k=>delete _ctxSigCache[k]);
    Object.keys(_l5Cache).forEach(k=>delete _l5Cache[k]);
  }

  return {
    configure, scoreCMP, getContextSignals, scoreOverLine, scoreUnderLine,
    verdict, drLine, getDVPPct, muPct, muInfo, getL5Avg, getRecentAvg, getHitRate,
    POS_TO_DVP
  };
})();
