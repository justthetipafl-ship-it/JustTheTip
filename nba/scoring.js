/* ============================================================
   scoring.js — JTT NBA+WNBA scoring engine (build C)
   ============================================================
   scoreCMP + the over/under wrappers + verdict + cmpFactors ported
   VERBATIM from the AFL gold standard (weights, thresholds, blend,
   variance and season-high guards untouched). The context-signals
   matrix is re-derived as market x {G,F,C} with basketball inputs:
   pace, allowed-by-defence (_a) columns, forced turnovers, shot
   volume, and player-affinity adjustments (3PA / assist / rebound
   rate vs position pool).

   Wire-up:
     JTTScoring.configure({ players, teams, teamsForm, dvp, logsByName,
                            currentSeason, minutesFloor, league });
   then:
     JTTScoring.scoreCMP(player, statKey, line, oppTeam)
     JTTScoring.scoreOverLine / scoreUnderLine (statKey-generic)
     JTTScoring.getContextSignals / getDVPPct / verdict / cmpFactors

   Signals whose inputs aren't present return null and are skipped —
   the matrix degrades gracefully rather than mis-firing.

   NOTE (ported fix): AFL's configure never assigned TD from ctx.teams,
   leaving muPct permanently null. Fixed here; flag for the AFL tool.
   ============================================================ */
window.JTTScoring = (function () {
  "use strict";

  // ---- module data (set by configure) ----
  let PD = [], TD = [], TF = [], DVP = [], _dvpIdx = {}, CUR = "2026";
  let BA = {}, MIN_FLOOR = 15, LEAGUE = "nba";
  const _ctxSigCache = {}, _l5Cache = {}, _pdPoolAvgs = {};

  // ---- constants ----
  // five-position labels fold into the three DVP buckets
  const POS_TO_DVP = { G:'G', PG:'G', SG:'G', F:'F', SF:'F', PF:'F', GF:'F', C:'C', FC:'C' };
  // statKey -> DVP table field (identity — DVP rows use internal keys)
  const DVP_STAT_MAP = {
    points:'points', rebounds:'rebounds', assists:'assists', threes:'threes',
    steals:'steals', blocks:'blocks', turnovers:'turnovers',
    pra:'pra', pr:'pr', pa:'pa', ra:'ra', stocks:'stocks'
  };
  // stat -> its "_a" (allowed) team key, used for muPct in scoreCMP
  const ALL = [
    {k:'points',a:'points_a'},{k:'rebounds',a:'rebounds_a'},{k:'assists',a:'assists_a'},
    {k:'threes',a:'threes_a'},{k:'threesAtt',a:'threesAtt_a'},{k:'fgm',a:'fgm_a'},
    {k:'fga',a:'fga_a'},{k:'ftm',a:'ftm_a'},{k:'fta',a:'fta_a'},
    {k:'oreb',a:'oreb_a'},{k:'dreb',a:'dreb_a'},{k:'steals',a:'steals_a'},
    {k:'blocks',a:'blocks_a'},{k:'turnovers',a:'turnovers_a'},
    {k:'pra',a:'points_a'},{k:'pr',a:'points_a'},{k:'pa',a:'points_a'},
    {k:'ra',a:'rebounds_a'},{k:'stocks',a:'steals_a'}
  ];
  function getStat(k){ return ALL.find(s=>s.k===k); }

  // ---- accessors (bound to split-data model) ----
  // Representative-game filter: minutes >= league floor (AFL's TOG>=50
  // analogue). DNP rows never reach the logs (pipeline drops them), but
  // guard anyway. Unknown minutes -> keep (better than dropping to zero data).
  function _isValidMinGame(r){
    if(!r) return true;
    if(r.dnp) return false;
    const raw = r.minutes!=null ? r.minutes : null;
    if(raw==null) return true;
    const t = parseFloat(raw);
    if(isNaN(t)) return true;
    return t >= MIN_FLOOR;
  }
  function dvpByName(name){ return (_dvpIdx[name]||[]).filter(_isValidMinGame); }
  function dvpByNameRaw(name){ return _dvpIdx[name] || []; }
  function isCurSeason(r){ return String(r.Year) === CUR; }
  function getPlayerPos(p){ return p.position; }
  function teamAbbrev(t){ return t; }   // data is abbreviation-native throughout
  function pdToLogKey(k){ return k; }   // players + logs share internal keys

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
  // full soft->tough ranking for a (pos, stat): [{team, val, pct}] desc by allowed
  function dvpRanked(posGroup, statKey){
    const avg = getDVPAvg(posGroup, statKey);
    return DVP.filter(d=>d.pos===posGroup && d[statKey]!=null)
      .map(d=>({team:d.team, val:d[statKey], pct:avg?((d[statKey]-avg)/avg)*100:null}))
      .sort((a,b)=>b.val-a.val);
  }

  // ---- team allowed/for/form percentages vs league average ----
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
  function getHitRate(name, statKey, line, curOnly){
    let games = dvpByName(name);
    if(curOnly) games = games.filter(isCurSeason);
    if(games.length<3) return null;
    const hits = games.filter(g=>(g[statKey]||0)>=line).length;
    return {rate:hits/games.length, n:games.length};
  }

  // ---- position-pool averages (affinity) ----
  function buildPDPoolAvgs(){
    Object.keys(_pdPoolAvgs).forEach(k=>delete _pdPoolAvgs[k]);
    const groups = {};
    PD.forEach(p=>{ const pg=POS_TO_DVP[p.position]||p.position||'Unknown'; (groups[pg]=groups[pg]||[]).push(p); });
    const POOL = { threesAtt:'threesAtt', assists:'assists', rebounds:'rebounds',
                   fga:'fga', oreb:'oreb', minutes:'minutes' };
    Object.entries(groups).forEach(([pg, players])=>{
      _pdPoolAvgs[pg]={};
      Object.entries(POOL).forEach(([poolKey, field])=>{
        const vals=players.map(p=>+(p[field]||0)).filter(v=>v>0);
        if(vals.length) _pdPoolAvgs[pg][poolKey]=vals.reduce((a,b)=>a+b,0)/vals.length;
      });
    });
  }

  // League averages over EVERY numeric key (not just "_a") so the matrix can
  // read FOR-side context too: opponent pace, own-turnover rates, rim protection.
  function _avgsOf(rows){
    const a={}; if(!rows||!rows.length) return a;
    const keys = Object.keys(rows[0]).filter(k=>typeof rows[0][k]==='number');
    keys.forEach(k=>{ const v=rows.map(r=>r[k]).filter(x=>typeof x==='number'&&isFinite(x)); if(v.length)a[k]=v.reduce((s,x)=>s+x,0)/v.length; });
    return a;
  }

  /* ============== CONTEXT SIGNALS MATRIX (basketball) ==============
     Semantics of keys read per opponent team row:
       "<stat>_a"  = what the opponent CONCEDES per game (soft = high)
       "<stat>"    = the opponent's OWN output (context, e.g. pace, their
                     turnovers feeding steals, their rim protection)
     Positive weight  -> more of it = good for the player's OVER.
     Negative weight  -> more of it = suppressive.                       */
  function getContextSignals(p, statType, opp){
    const cacheKey=(p.name||'')+'|'+(statType||'')+'|'+(opp||'');
    if(_ctxSigCache[cacheKey]) return _ctxSigCache[cacheKey];
    const _r=_getContextSignalsInner(p, statType, opp);
    _ctxSigCache[cacheKey]=_r; return _r;
  }
  function _getContextSignalsInner(p, statType, opp){
    const pos=POS_TO_DVP[p.position]||p.position||'';
    const isG=pos==='G', isF=pos==='F', isC=pos==='C';
    const signals=[];
    function add(label,tdKey,tfKey,weight){
      const td=tdKey?tdPctFor(opp,tdKey):null;
      const tf=tfKey?tfPctFor(opp,tfKey):null;
      const pct=tf!==null?tf:td;
      if(pct!==null) signals.push({label,pct,weight,src:tf!==null?'form':'season'});
    }
    function addI(label,tdKey,tfKey,weight,playerField,poolKey){
      const td=tdKey?tdPctFor(opp,tdKey):null;
      const tf=tfKey?tfPctFor(opp,tfKey):null;
      const oppPct=tf!==null?tf:td;
      if(oppPct===null) return;
      const pg=POS_TO_DVP[p.position]||p.position||'';
      const pool=_pdPoolAvgs[pg]||{};
      const poolAvg=pool[poolKey]||null;
      const pVal=+(p[playerField]||0);
      let affinity=1.0;
      if(poolAvg&&poolAvg>0){ const rel=(pVal-poolAvg)/poolAvg; affinity=Math.max(0.5,Math.min(1.6,1+rel)); }
      const adjPct=oppPct*affinity;
      const tag=affinity>1.25?' (strength match)':affinity<0.75?' (poor fit)':'';
      signals.push({label:label+tag,pct:adjPct,weight,src:tf!==null?'form+player':'season+player'});
    }

    if(statType==='points'||statType==='pra'||statType==='pr'||statType==='pa'){
      if(isG){
        add('Pts Allowed','points_a','points_a',1.4);
        add('Pace','pace','pace',1.0);
        addI('3PM Allowed','threes_a','threes_a',0.9,'threesAtt','threesAtt');
        add('FGA Allowed','fga_a','fga_a',0.8);
        add('FTA Allowed','fta_a','fta_a',0.5);
        add('Opp Forces TO','turnovers_a','turnovers_a',-0.6);
      }
      if(isF){
        add('Pts Allowed','points_a','points_a',1.4);
        add('Pace','pace','pace',1.0);
        add('FGA Allowed','fga_a','fga_a',0.8);
        add('OREB Allowed','oreb_a','oreb_a',0.6);
        add('FTA Allowed','fta_a','fta_a',0.6);
        add('Opp Forces TO','turnovers_a','turnovers_a',-0.5);
      }
      if(isC){
        add('Pts Allowed','points_a','points_a',1.3);
        addI('OREB Allowed','oreb_a','oreb_a',0.9,'oreb','oreb');
        add('FTA Allowed','fta_a','fta_a',0.8);
        add('Pace','pace','pace',0.9);
        add('Opp Rim Protection','blocks',null,-0.8);
      }
      // combo markets pick up the secondary stat context below
      if(statType==='pra'||statType==='pr'||statType==='ra'){
        add('Reb Allowed','rebounds_a','rebounds_a',0.8);
      }
      if(statType==='pra'||statType==='pa'){
        add('Ast Allowed','assists_a','assists_a',0.8);
      }
    }

    if(statType==='rebounds'||statType==='ra'){
      if(isC){
        addI('Reb Allowed','rebounds_a','rebounds_a',1.4,'rebounds','rebounds');
        add('OREB Allowed','oreb_a','oreb_a',1.1);
        add('Opp Shot Volume','fga',null,0.6);
        add('Opp Crashes Glass','oreb',null,-0.6);
        add('Pace','pace','pace',0.8);
      }
      if(isF){
        addI('Reb Allowed','rebounds_a','rebounds_a',1.3,'rebounds','rebounds');
        add('OREB Allowed','oreb_a','oreb_a',0.9);
        add('Pace','pace','pace',0.8);
        add('Opp Shot Volume','fga',null,0.5);
      }
      if(isG){
        add('Reb Allowed','rebounds_a','rebounds_a',1.2);
        add('Pace','pace','pace',0.9);
        add('DREB Allowed','dreb_a','dreb_a',0.6);
      }
      if(statType==='ra') add('Ast Allowed','assists_a','assists_a',0.8);
    }

    if(statType==='assists'){
      if(isG){
        addI('Ast Allowed','assists_a','assists_a',1.4,'assists','assists');
        add('Pace','pace','pace',1.0);
        add('3PM Allowed','threes_a','threes_a',0.8);
        add('FGM Allowed','fgm_a','fgm_a',0.7);
        add('Opp Forces TO','turnovers_a','turnovers_a',-0.8);
      }
      if(isF){
        addI('Ast Allowed','assists_a','assists_a',1.2,'assists','assists');
        add('Pace','pace','pace',0.9);
        add('FGM Allowed','fgm_a','fgm_a',0.6);
        add('Opp Forces TO','turnovers_a','turnovers_a',-0.6);
      }
      if(isC){
        add('Ast Allowed','assists_a','assists_a',1.1);
        add('FGM Allowed','fgm_a','fgm_a',0.6);
        add('Pace','pace','pace',0.7);
      }
    }

    if(statType==='threes'){
      const w=isG?1.0:isF?0.9:0.75;
      addI('3PM Allowed','threes_a','threes_a',1.5*w,'threesAtt','threesAtt');
      add('3PA Allowed','threesAtt_a','threesAtt_a',1.1*w);
      add('Pace','pace','pace',0.9*w);
      add('Pts Allowed','points_a','points_a',0.4*w);
    }

    if(statType==='steals'||statType==='stocks'){
      add('Opp Turnovers','turnovers',null,1.3);
      add('Pace','pace','pace',1.0);
      add('Opp Shot Volume','fga',null,0.4);
    }
    if(statType==='blocks'||statType==='stocks'){
      if(isC||isF){
        add('Opp Shot Volume','fga',null,1.0);
        add('Opp 3PA (unblockable)','threesAtt',null,-0.6);
        add('Pace','pace','pace',0.8);
      } else {
        add('Opp Shot Volume','fga',null,0.6);
        add('Pace','pace','pace',0.6);
      }
    }

    if(statType==='turnovers'){
      add('Opp Forces TO','turnovers_a','turnovers_a',1.3);
      add('Opp Steals','steals',null,1.1);
      add('Pace','pace','pace',0.7);
    }

    return signals;
  }

  /* ============== ENGINE (verbatim from AFL) ============== */
  function drLine(avg){ if(avg<1) return null; return Math.round(avg); }

  function scoreCMP(p, statKey, line, opp){
    const logKey=pdToLogKey(statKey);
    const allGames=dvpByName(p.name);
    const gamesCur=allGames.filter(r=>isCurSeason(r));
    const logGames=allGames;
    const pdAvg=p[statKey]||0;
    const avgCurVals=gamesCur.map(r=>r[logKey]||0);
    const avg=avgCurVals.length?avgCurVals.reduce((a,b)=>a+b,0)/avgCurVals.length:pdAvg;
    const l5=getL5Avg(p.name,statKey);
    const l10=getRecentAvg(p.name,statKey,10);
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
    const lastVsOpp=vsOppGames.slice().sort((a,b)=>((b.Date||'')<(a.Date||'')?-1:1))[0]||null;
    const lastVsOppVal=lastVsOpp?(lastVsOpp[logKey]||0):null;
    const allSignals=opp?getContextSignals(p,statKey,opp):[];

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

  // ---- over / under wrappers (statKey-generic; AFL thresholds intact) ----
  function scoreUnderLine(p, opp, line, statKey){
    statKey=statKey||'points';
    if(!line || (p.minutes||0)<MIN_FLOOR+3) return null;
    const gamesCur=dvpByName(p.name).filter(isCurSeason);
    if(gamesCur.length<3) return null;
    const dvpPos=POS_TO_DVP[getPlayerPos(p)]||getPlayerPos(p);
    const dvpSk=DVP_STAT_MAP[statKey];
    const dvpPct=dvpSk&&opp?getDVPPct(teamAbbrev(opp),dvpPos,dvpSk):null;
    if(dvpPct!==null&&dvpPct>=0) return null;
    const score=scoreCMP(p,statKey,line,opp);
    const avg=p[statKey]||0;
    const vals=gamesCur.map(r=>r[statKey]||0);
    const hits=vals.filter(v=>v>=line).length;
    const hitRate=hits/vals.length;
    const l5=getL5Avg(p.name,statKey);
    let verdictLabel,verdictCol;
    if(score<=-3.5){verdictLabel='Strong Lean UNDER';verdictCol='#ef4444';}
    else if(score<=-2){verdictLabel='Lean UNDER';verdictCol='#f97316';}
    else {verdictLabel='No Clear Edge';verdictCol='#888';}
    if(score>-3.5) return null;
    const baseL=drLine(avg);
    return {p,opp,avg,line,l5,hitRate,hits,total:vals.length,dvpPct,score,verdictLabel,verdictCol,gamesCur:vals.length,isCustom:line!==baseL,statKey};
  }
  function scoreOverLine(p, opp, line, statKey){
    statKey=statKey||'points';
    if(!line || (p.minutes||0)<MIN_FLOOR+3) return null;
    const gamesCur=dvpByName(p.name).filter(isCurSeason);
    if(gamesCur.length<3) return null;
    const dvpPos=POS_TO_DVP[getPlayerPos(p)]||getPlayerPos(p);
    const dvpSk=DVP_STAT_MAP[statKey];
    const dvpPct=dvpSk&&opp?getDVPPct(teamAbbrev(opp),dvpPos,dvpSk):null;
    if(dvpPct!==null&&dvpPct<=0) return null;
    const score=scoreCMP(p,statKey,line,opp);
    if(score===null) return null;
    const avg=p[statKey]||0;
    const vals=gamesCur.map(r=>r[statKey]||0);
    const hits=vals.filter(v=>v>=line).length;
    const hitRate=hits/vals.length;
    const l5=getL5Avg(p.name,statKey);
    let verdictLabel,verdictCol;
    if(score>=5.5){verdictLabel='Green Light';verdictCol='#22c55e';}
    else if(score>=3.5){verdictLabel='Lean OVER';verdictCol='#86efac';}
    else {verdictLabel='No Clear Edge';verdictCol='#888';}
    if(score<5.5) return null;
    const baseL=drLine(avg);
    return {p,opp,avg,line,l5,hitRate,hits,total:vals.length,dvpPct,score,verdictLabel,verdictCol,gamesCur:vals.length,isCustom:line!==baseL,statKey};
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
    const gamesCur=allGames.filter(r=>isCurSeason(r));
    const logGames=allGames;
    const pdAvg=p[statKey]||0;
    const aC=gamesCur.map(r=>r[logKey]||0);
    const avg=aC.length?aC.reduce((x,y)=>x+y,0)/aC.length:pdAvg;
    const l5=getL5Avg(p.name,statKey);
    const _l3g=allGames.slice(-3);
    const l3=_l3g.length>=3?_l3g.reduce((s,r)=>s+(r[logKey]||0),0)/_l3g.length:null;
    const seasonHigh=gamesCur.reduce((mx,r)=>Math.max(mx,r[logKey]||0),0);
    const hits=logGames.filter(r=>(r[logKey]||0)>=line).length;
    const hitRateRaw=logGames.length?hits/logGames.length:null;
    const hrCRaw=gamesCur.length?gamesCur.filter(r=>(r[logKey]||0)>=line).length/gamesCur.length:null;
    const _bN=gamesCur.length, _bW=_bN/(_bN+8);
    const hitRate=hrCRaw!==null&&hitRateRaw!==null?_bW*hrCRaw+(1-_bW)*hitRateRaw:hitRateRaw;
    const hitRateCur=hrCRaw;
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
    const lastVs=vsOpp.slice().sort((a,b)=>((b.Date||'')<(a.Date||'')?-1:1))[0]||null;
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
      const rk=dvpRank?` (#${dvpRank}/${dvpTotal} ${dvpRank<=Math.ceil((dvpTotal||30)/2)?'softest':'toughest'})`:'';
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
      const rl=lastVs.Date?` (${lastVs.Date})`:'';
      const hist=vsOpp.length>1?` · ${vsOppHits}/${vsOpp.length} historical vs ${opp}`:'';
      if(hit) push('good',`Last vs ${opp}${rl}: ${lastVsVal.toFixed(1)} ${lbl} — hit the line${hist}`);
      else { const close=lastVsVal>=line*0.8; push(close?'neutral':'warn',`Last vs ${opp}${rl}: ${lastVsVal.toFixed(1)} ${lbl} — ${close?'just missed':'below the line'}${hist}`); }
    } else if(opp) push('neutral',`No previous games vs ${opp} in log`);
    const sig=opp?getContextSignals(p,statKey,opp):[];
    if(sig&&sig.length){
      sig.filter(s=>Math.abs(s.pct)>=5).sort((a,b)=>Math.abs(b.pct*b.weight)-Math.abs(a.pct*a.weight)).slice(0,5).forEach(s=>{
        const c=s.pct*s.weight, pos=c>0, isSup=s.weight<0;
        const tone=c>15?'good':c>5?'lean':c>-5?'neutral':c>-15?'warn':'bad';
        const suf=isSup?(pos?' — less than avg (good)':' — more than avg (bad)'):'';
        push(tone,`${opp} — ${s.label}: ${s.pct>=0?'+':''}${s.pct.toFixed(0)}% vs league avg${suf}`);
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
    PD=ctx.players||[];
    TD=ctx.teams||[];                    // (AFL bug fixed: TD was never assigned)
    TF=ctx.teamsForm||ctx.teams||[];
    DVP=ctx.dvp||[];
    _dvpIdx=ctx.logsByName||{};
    CUR=String(ctx.currentSeason||new Date().getFullYear());
    MIN_FLOOR=ctx.minutesFloor!=null?ctx.minutesFloor:15;
    LEAGUE=ctx.league||'nba';
    BA=_avgsOf(TD);
    TF._avgs=_avgsOf(TF);
    buildPDPoolAvgs();
    Object.keys(_ctxSigCache).forEach(k=>delete _ctxSigCache[k]);
    Object.keys(_l5Cache).forEach(k=>delete _l5Cache[k]);
  }

  return {
    configure, scoreCMP, cmpFactors, getContextSignals, scoreOverLine, scoreUnderLine,
    verdict, drLine, getDVPPct, dvpRanked, getDVPAvg, muPct, muInfo,
    getL5Avg, getRecentAvg, getHitRate, POS_TO_DVP
  };
})();
