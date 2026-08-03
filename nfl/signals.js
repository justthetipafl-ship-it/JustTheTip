/* ============================================================================
 * JTT — nfl/signals.js  (NFL signal module: browser + Node)
 * ----------------------------------------------------------------------------
 * Sibling of AFL/signals.js. ONE code path for every fired NFL signal: the
 * unified shell (index.html) and the pre-slate capture job (Node) both import
 * THIS file, so the ledger grades the exact model the subscriber sees.
 *
 *   Browser:  loaded by the shell's _ensureScripts()  ->  window.JTTSignals
 *   Node:     const JTTSignals = require('./nfl/signals.js');
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The shell delegates Green Lights / Death Riders to JTTSignals.collectOU. AFL's
 * collectOU is disposals-only; NFL's is MULTI-MARKET (8 markets), and every row it
 * emits carries `market` + `mktLabel` so the shell's signalCard / ouSection can
 * render whichever market fired. The 13 research tiles that were inline in
 * nfl/index.html live here too — the shell keeps its own AFL copies, gated to
 * SPORT.key==='afl'.
 *
 * PORTING NOTE (important, and easy to get wrong)
 * -----------------------------------------------
 * realisticLine2() and sameDivision() are NFL-SPECIFIC and ship inside this module
 * on purpose. The shell has a same-named realisticLine2 built on AFL markets
 * (disposals/goals). If NFL injected the shell's version instead, Streakers and the
 * Signal Ledger would compute AFL lines against NFL stats and fail silently — the
 * worst kind of bug, because it still renders. windowDvp / estimateHitProb ARE
 * byte-identical across sports, so those are injected.
 *
 * The generators need tool-internal helpers (oddsFor, nextOpp, JTTScoring, state, …),
 * so this module is a DEPENDENCY-INJECTION FACTORY, exactly like AFL/signals.js:
 * JTTSignals.create(deps) -> { collectOU, tiles, capture*, … }.
 * ========================================================================== */
(function (root, factory) {
  var mod = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = mod;   // Node
  root.JTTSignals = mod;                                                        // browser
})(typeof self !== 'undefined' ? self : (typeof globalThis !== 'undefined' ? globalThis : this), function () {
  'use strict';

  /* ============================================================================
   * CANONICAL SETTLEABLE-SIGNAL RECORD  (same contract as AFL/signals.js)
   * ----------------------------------------------------------------------------
   * Only one sport's signals.js is loaded at a time, so the ledger core is
   * duplicated here rather than shared — the module must stand alone in Node.
   *
   * HONESTY RULES (enforced by grade()):
   *   1. DNP / inactive / 0 snaps -> 'void', never 'loss'.
   *   2. Grade the CAPTURED line only, never one recomputed at settle time.
   *   3. Milestone (X+) uses >= ; two-way (X.5) uses strict >/< with push on exact.
   *   4. Flat 1u at the captured price. No compounding, no staking model.
   * ========================================================================== */
  var SIGNAL_DEFS = {
    green_light: { label:'Green Lights',    market:'dynamic',   side:'over',  lineType:'twoway',    priced:true },
    death_rider: { label:'Death Riders',    market:'dynamic',   side:'under', lineType:'twoway',    priced:true },
    tuddy:       { label:'Tuddy Targets',   market:'anytimeTd', side:'over',  lineType:'milestone', priced:true },
    elite:       { label:'Elite Matchups',  market:'dynamic',   side:'over',  lineType:'twoway',    priced:true },
    bunnies:     { label:'Divisional Bunnies', market:'dynamic', side:'over', lineType:'twoway',    priced:true },
    bogey:       { label:'Divisional Bogey',   market:'dynamic', side:'under',lineType:'twoway',    priced:true },
    streakers:   { label:'Streakers',       market:'dynamic',   side:'over',  lineType:'milestone', priced:true },
    chunk:       { label:'Chunk Plays',     market:'dynamic',   side:'over',  lineType:'twoway',    priced:true },
    wrap:        { label:'Tackle Machines', market:'tackles',   side:'over',  lineType:'twoway',    priced:true }
  };

  function _round(x) { return x == null ? '' : ('W' + x); }
  function makeId(season, round, signalType, player, market, line, side) {
    return [season, _round(round), signalType, player, market, line, side].join('|');
  }

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
      closePrice: null, result: null, actual: null, settledAt: null
    };
  }

  function grade(rec, actual, played) {
    if (!played || actual == null || !isFinite(actual)) return { result:'void', actual:null };
    var L = rec.line, over = rec.side === 'over', hit;
    if (rec.lineType === 'milestone') {
      hit = actual >= L;
      return { result: over ? (hit ? 'win' : 'loss') : (hit ? 'loss' : 'win'), actual: actual };
    }
    if (actual === L) return { result:'push', actual:actual };
    hit = actual > L;
    return { result: over ? (hit ? 'win' : 'loss') : (hit ? 'loss' : 'win'), actual: actual };
  }

  function pnl(rec) {
    if (rec.result === 'win')  return (rec.price != null ? rec.price - 1 : 0);
    if (rec.result === 'loss') return -1;
    return 0;
  }

  function rollup(rows) {
    var by = {};
    (rows || []).forEach(function (r) {
      var b = by[r.signalType] || (by[r.signalType] = { n:0, wins:0, losses:0, pushes:0, voids:0, units:0 });
      if (r.result === 'void' || r.result == null) { b.voids++; return; }
      b.n++;
      if (r.result === 'win') b.wins++;
      else if (r.result === 'loss') b.losses++;
      else if (r.result === 'push') b.pushes++;
      b.units += pnl(r);
    });
    Object.keys(by).forEach(function (k) {
      var b = by[k], decided = b.wins + b.losses;
      b.hitRate = decided ? b.wins / decided : null;
      b.roi = b.n ? b.units / b.n : null;
    });
    return by;
  }

  /* ============================================================================
   * GENERATORS (dependency-injected)
   * ----------------------------------------------------------------------------
   * create(deps) -> generators bound to the caller's runtime. Deps mirror what the
   * shell's _signals() already injects for AFL, plus the state accessors the NFL
   * research tiles need. Missing optional deps degrade to a no-op rather than
   * throwing, so a tile whose feed hasn't landed yet renders empty instead of
   * taking down the whole Degen page.
   * ========================================================================== */
  function create(deps) {
    deps = deps || {};
    var JTTScoring      = deps.JTTScoring;
    var oddsFor         = deps.oddsFor        || function () { return null; };
    var nextOpp         = deps.nextOpp        || function () { return null; };
    var playersOnTeam   = deps.playersOnTeam  || function () { return []; };
    var hasAnyOdds      = deps.hasAnyOdds     || function () { return false; };
    var SIGNAL_MIN_ODDS = deps.SIGNAL_MIN_ODDS || { over:null, under:null };
    var priceForLine    = deps.priceForLine   || function () { return null; };
    var snapToRung      = deps.snapToRung     || function (n, m, t) { return t; };
    var logsFor         = deps.logsFor        || function () { return []; };
    var abbr            = deps.abbr           || function (t) { return String(t || '').slice(0,3).toUpperCase(); };
    var curSeason       = deps.curSeason      || function () { return ''; };

    // ---- state accessors (the research tiles read the loaded data directly) ----
    var state = deps.state || { data:{}, idx:{}, dataVersion:'' };
    var CUR_SEASON   = deps.curSeason || function () { return (state.data.meta && state.data.meta.currentSeason) || '2025'; };
    var _avg         = deps._avg         || function (a) { return a.length ? a.reduce(function (x,y) { return x+y; },0)/a.length : 0; };
    var allLogs      = deps.allLogs      || function (n) { return (state.idx.logsByName||{})[n] || []; };
    var curLogs      = deps.curLogs      || function (n) { return allLogs(n).filter(function (r) { return String(r.Year) === String(CUR_SEASON()); }); };
    var _fixtureSet  = deps.fixtureSet   || function () { return new Set((state.data.fixture||[]).reduce(function (a,g) { return a.concat([g.home,g.away]); }, []).filter(Boolean)); };
    var _roundNum    = deps.roundNum     || function (rd) { var m = String(rd||'').match(/\d+/); return m ? +m[0] : 0; };
    var isPlaying    = deps.isPlaying    || function () { return true; };
    var windowDvp    = deps.windowDvp    || function () { return null; };
    var estimateHitProb = deps.estimateHitProb || function () { return 0.5; };
    var _pairRho     = deps.pairRho      || function () { return 0; };
    var _clusterJoint= deps.clusterJoint || function (legs) { return (legs||[]).reduce(function (a,l) { return a*(l.prob||0); },1); };
    var _degenScope  = deps.degenScope   || null;

    // localStorage shim — the ledger caches in the browser, no-ops in Node.
    var _LS = deps.storage || (typeof localStorage !== 'undefined' ? localStorage
      : { getItem:function(){return null;}, setItem:function(){} });

    /* ---- render deps (identical across sports -> injected, not duplicated) ---- */
    var esc          = deps.esc          || function (s) { return String(s == null ? '' : s); };
    var fmt          = deps.fmt          || function (v, n) { return v == null ? '\u2014' : (+v).toFixed(n == null ? 1 : n); };
    var teamLogo     = deps.teamLogo     || function () { return ''; };
    var _byGame      = deps.byGame       || function (items, teamFn, rowFn) { return (items || []).map(rowFn); };
    var _fc          = deps.fc           || function (a) { return a || []; };
    var emptyState   = deps.emptyState   || function (i, t, s) { return '<div class="empty">' + t + '</div>'; };
    var lineTag      = deps.lineTag      || function () { return ''; };
    var multiLineTag = deps.multiLineTag || function () { return ''; };
    var underTag     = deps.underTag     || function () { return ''; };
    var streakOdds   = deps.streakOdds   || function () { return ''; };
    var muTag        = deps.muTag        || function () { return ''; };
    var formNote     = deps.formNote     || function () { return ''; };
    var _injuryFor   = deps.injuryFor    || function () { return null; };
    var _attrEsc     = deps.attrEsc      || function (s) { return ('' + s).replace(/&/g,'&amp;').replace(/"/g,'&quot;'); };
    var _negTip = null, _negShow = null, _negHide = null;   // tooltip handlers live on window in the browser
    var _degenFocused= !!deps.degenFocused;



    /* ---- tuning constants (verbatim from nfl/index.html) ---- */
    const OU_MARKETS=[['passYds','Pass Yds'],['rushYds','Rush Yds'],['recYds','Rec Yds'],['receptions','Receptions'],['rushAtt','Rush Att'],['passAtt','Pass Att'],['rushRecYds','Rush+Rec Yds'],['tackles','Tackles+Ast']];
    const ELITE_DEFS = [{k:'passYds',l:'Pass Yds'},{k:'rushYds',l:'Rush Yds'},{k:'recYds',l:'Rec Yds'},
      {k:'receptions',l:'Receptions'},{k:'rushAtt',l:'Rush Att'},{k:'targets',l:'Targets'},{k:'tackles',l:'Tackles+Ast'}];
    const BUNNY_STATS = [{k:'recYds',l:'Rec Yds',min:30},{k:'receptions',l:'Receptions',min:3},
      {k:'rushYds',l:'Rush Yds',min:30},{k:'passYds',l:'Pass Yds',min:150}];
    const BOGEY_STATS = [{k:'recYds',l:'Rec Yds',min:30},{k:'rushYds',l:'Rush Yds',min:30},{k:'passYds',l:'Pass Yds',min:150}];
    const STREAK_STATS = [['passYds','Pass Yds'],['rushYds','Rush Yds'],['recYds','Rec Yds'],['receptions','Receptions'],
      ['rushAtt','Rush Att'],['rushRecYds','Rush+Rec Yds'],['anytimeTd','Anytime TD'],['tackles','Tackles+Ast']];
    const _TUDDY_STATS={
      WR:[['rzTgt','RZ targets'],['rzTgtPct','RZ target %'],['rzRec','RZ receptions'],['rzRecTd','RZ TDs'],
          ['i10Tgt','inside-10 targets'],['i10TgtPct','inside-10 target %'],['i10Rec','inside-10 receptions'],['i10RecTd','inside-10 TDs']],
      RB:[['rzTgt','RZ targets'],['rzTgtPct','RZ target %'],['rzRec','RZ receptions'],['rzRecTd','RZ receiving TDs'],
          ['rzAtt','RZ carries'],['rzRushPct','RZ rush %'],['rzRushTd','RZ rush TDs'],
          ['i10Att','inside-10 carries'],['i10RushPct','inside-10 rush %'],['i10RushTd','inside-10 TDs'],
          ['i5Att','inside-5 carries'],['i5RushPct','inside-5 rush %'],['i5RushTd','inside-5 TDs']],
      QB:[['rzAtt','RZ carries'],['rzRushPct','RZ rush %'],['rzRushTd','RZ rush TDs']]
    };
    const CHUNK_DEFS=[
      {k:'longRec', l:'Longest Rec', thr:20, pos:['WR','TE','RB'], exp:'expRec',  expL:'explosive catches', style:'aDot'},
      {k:'longRush',l:'Longest Rush',thr:12, pos:['RB','QB'],      exp:'expRush', expL:'explosive runs',    style:'ypc'},
      {k:'longComp',l:'Longest Comp',thr:35, pos:['QB'],           exp:'expRec',  expL:'explosive passes',  style:null}
    ];
    const CHUNK_HIT=0.70, CHUNK_MIN_G=6, CHUNK_CLAMP=-12;
    const WRAP_HIT=0.65, WRAP_MIN_G=6, WRAP_STARVE=-15;
    const USAGE_MIN_G=6;
    const WX_WIND=25;
    const DEF_POS=new Set(['LB','DL','DB']);
    const ALERT_THRESHOLD = 0.25, ALERT_MIN_L3 = 3;

    let _ledgerCache=null;

    /* ---- NFL-specific helpers ---- */
    function realisticLine2(k,avg){
      if(k==='passYds'){ if(avg<180)return null; const l=Math.round(avg)-25; return l>=150?l:null; }
      if(k==='passAtt'){ if(avg<28)return null; const l=Math.round(avg)-4; return l>=24?l:null; }
      if(k==='rushYds'||k==='recYds'){ if(avg<40)return null; const l=Math.round(avg)-10; return l>=30?l:null; }
      if(k==='rushRecYds'){ if(avg<50)return null; const l=Math.round(avg)-12; return l>=40?l:null; }
      if(k==='receptions'){ if(avg<3.5)return null; const l=Math.floor(avg)-1; return l>=2?l:null; }
      if(k==='rushAtt'){ if(avg<10)return null; const l=Math.round(avg)-3; return l>=8?l:null; }
      if(k==='anytimeTd'){ return avg>=0.55?1:null; }               // scored a TD
      if(k==='tackles'){ if(avg<6.5)return null; const l=Math.floor(avg)-1; return l>=5?l:null; }
      return null;
    }
    function sameDivision(a,b){ const ta=state.idx.teamMap[a], tb=state.idx.teamMap[b]; return !!(ta&&tb&&ta.division&&ta.division===tb.division); }
    function dvpRank(opp, position, stat){
      const rows=(state.data.dvp||[]).filter(r=>r.pos===position);
      if(!rows.length||rows[0][stat]==null) return null;
      const oppRow=rows.find(r=>r.team===opp); if(!oppRow) return null;
      const vals=rows.map(r=>r[stat]||0);
      const avg=vals.reduce((a,b)=>a+b,0)/vals.length;
      const sorted=[...vals].sort((a,b)=>b-a);   // desc: rank 1 = allows the most = softest
      return {rank:sorted.indexOf(oppRow[stat]||0)+1, total:rows.length,
              pct:avg?((oppRow[stat]-avg)/avg*100):0, allowed:oppRow[stat]};
    }
    function _tdRate(logs){ const n=logs.length; return {h:logs.filter(r=>(r.totalTds||0)>=1).length,n}; }
    function _lgAvg(funnel,pos){
      const vs=Object.keys(funnel).filter(k=>k.endsWith('|'+pos)).map(k=>funnel[k].sum/Math.max(1,funnel[k].g.size));
      return vs.length?vs.reduce((s,v)=>s+v,0)/vs.length:1;
    }
    function _oppVolume(opp){
      const arr=Object.values(state.idx.teamMap||{}).filter(t=>t.plays!=null);
      if(!arr.length||!state.idx.teamMap[opp]) return null;
      const t=state.idx.teamMap[opp];
      const pr=[...arr].sort((a,b)=>(b.plays||0)-(a.plays||0)).findIndex(x=>x.team===opp)+1;
      const rr=[...arr].sort((a,b)=>(b.rushAtt||0)-(a.rushAtt||0)).findIndex(x=>x.team===opp)+1;
      return {plays:t.plays,playsRank:pr,rushAtt:t.rushAtt,rushRank:rr,n:arr.length};
    }
    function _outList(){
      const outRe=/out|injured reserve|\bir\b|doubt/i;
      return (state.data.injury||[]).filter(r=>outRe.test(String(r.Status||'')));
    }
    function _standings(){
      const res=state.data.results||[]; if(!res.length) return null;
      const played=s=>res.filter(r=>String(r.season)===String(s)&&r.hs!=null&&r.as!=null);
      let season=+CUR_SEASON(), rows=played(season), usedPrev=false;
      if(rows.length<16){
        const seasons=[...new Set(res.map(r=>+r.season))].filter(Boolean).sort((a,b)=>b-a);
        const s2=seasons.find(s=>played(s).length>=32);
        if(s2==null) return null;
        usedPrev=s2!==season; season=s2; rows=played(s2);
      }
      const rec={};
      rows.forEach(r=>{
        const add=(t,w,l,ti)=>{ const o=rec[t]=rec[t]||{w:0,l:0,t:0}; o.w+=w; o.l+=l; o.t+=ti; };
        if(r.hs>r.as){ add(r.home,1,0,0); add(r.away,0,1,0); }
        else if(r.as>r.hs){ add(r.away,1,0,0); add(r.home,0,1,0); }
        else { add(r.home,0,0,1); add(r.away,0,0,1); }
      });
      const conf={};
      Object.entries(rec).forEach(([t,o])=>{
        const tm=state.idx.teamMap[t]; const c=(tm&&tm.conference)||'—';
        const g=o.w+o.l+o.t;
        (conf[c]=conf[c]||[]).push({team:t,w:o.w,l:o.l,t:o.t,pct:g?(o.w+0.5*o.t)/g:0});
      });
      Object.values(conf).forEach(a=>{ a.sort((x,y)=>(y.pct-x.pct)||(y.w-x.w)); a.forEach((r,i)=>r.seed=i+1); });
      return {conf,season,usedPrev};
    }
    function _stackProb(name,statKey,line){
      const hr=JTTScoring.getHitRate(name,statKey,line,true);
      if(hr&&hr.n>=6) return {prob:hr.rate,n:hr.n};
      const p=state.idx.byName[name];
      return {prob:p?estimateHitProb(p[statKey]||0,line):0.5,n:0};
    }
    function _chunkSimilar(p,def,opp,line){
      if(!opp) return null;
      const metric=def.k==='longRush'?'ypc':(def.k==='longComp'?'passYds':'aDot');
      const mine=p[metric]||0; if(!mine) return null;
      const pool=(state.idx.players||[]).filter(q=>q.name!==p.name&&q.position===p.position&&(q.matches||0)>=3&&(q[metric]||0)>0)
        .map(q=>({q,d:Math.abs((q[metric]||0)-mine)})).sort((a,b)=>a.d-b.d).slice(0,8);
      let h=0,n=0,used=0;
      pool.forEach(({q})=>{
        const vs=(state.idx.logsByName[q.name]||[]).filter(r=>r.opponent===opp&&r[def.k]!=null);
        if(!vs.length) return;
        used++;
        vs.forEach(r=>{ n++; if((r[def.k]||0)>=line) h++; });
      });
      return n>=3?{h,n,used}:null;
    }
    function _chunkStyle(p,def){
      if(def.style==='aDot'){
        const a=p.aDot||0; if(!a) return null;
        if(a>=12) return {txt:'deep threat · aDot '+a.toFixed(1),pts:8,c:'#22c55e'};
        if(a<=8)  return {txt:'short-area · aDot '+a.toFixed(1),pts:-8,c:'#f97316'};
        return {txt:'aDot '+a.toFixed(1),pts:0,c:'var(--text-3)'};
      }
      if(def.style==='ypc'){
        const y=p.ypc||0; if(!y) return null;
        if(y>=4.8) return {txt:'explosive runner · '+y.toFixed(1)+' ypc',pts:6,c:'#22c55e'};
        if(y<=3.6) return {txt:'grinder · '+y.toFixed(1)+' ypc',pts:-6,c:'#f97316'};
        return {txt:y.toFixed(1)+' ypc',pts:0,c:'var(--text-3)'};
      }
      return null;
    }

    /* ---- signal generators ---- */
    function collectOU(teamList, kind){
      if(!JTTScoring || !hasAnyOdds()) return [];
      const side=kind==='over'?'over':'under';
      const minOdds=SIGNAL_MIN_ODDS[side];
      const out=[];
      teamList.forEach(team=>{
        const opp=nextOpp(team); if(!opp) return;
        playersOnTeam(team).forEach(p=>{
          OU_MARKETS.forEach(([mk,ml])=>{
            const od=oddsFor(p.name,mk);                           // line must exist…
            if(!od || od.line==null) return;
            const price=od[side];
            if(price==null) return;                                // …and have a price this side
            if(minOdds!=null && price<minOdds) return;             // …above the signal's floor
            const r=kind==='over'?JTTScoring.scoreOverLine(p,opp,od.line,mk):JTTScoring.scoreUnderLine(p,opp,od.line,mk);
            if(r){ r.team=team; r.oppName=opp; r.odds=price; r.book=od.book; r.mktLabel=ml; out.push(r); }
          });
        });
      });
      return out.sort((a,b)=>kind==='over'?b.score-a.score:a.score-b.score);
    }
    function hotMatchups(defs){
      const teams=_fixtureSet(); const out=[];
      const pool=(state.idx.players||[]).filter(p=>teams.has(p.team)&&(p.matches||0)>=4);
      defs.forEach(def=>{
        [...pool].sort((a,b)=>(b[def.k]||0)-(a[def.k]||0)).slice(0,10).forEach((p,i)=>{
          const opp=nextOpp(p.team); if(!opp) return;
          const pos=JTTScoring.POS_TO_DVP[p.position]||p.position;
          const pct=JTTScoring.getDVPPct(opp,pos,def.k);
          if(pct==null||pct<=5) return;          // soft matchup only
          out.push({p,def,opp,dvpPct:pct,rank:i+1,val:p[def.k]||0});
        });
      });
      return out.sort((a,b)=>b.dvpPct-a.dvpPct);
    }
    function snags(){
      const rz=state.data.redzone||[]; if(!rz.length) return [];
      // per position, per stat: who's top-5 in the league
      const byPos={}; rz.forEach(r=>{ const p=state.idx.byName[r.player]; if(p&&_TUDDY_STATS[p.position])(byPos[p.position]=byPos[p.position]||[]).push(r); });
      const qual={};   // player name -> {p, chips:[{rank,l}]}
      Object.entries(byPos).forEach(([pos,rows])=>{
        _TUDDY_STATS[pos].forEach(([k,l])=>{
          rows.filter(r=>(r[k]||0)>0).sort((a,b)=>(b[k]||0)-(a[k]||0)).slice(0,5).forEach((r,i)=>{
            const p=state.idx.byName[r.player]; if(!p) return;
            const q=qual[r.player]=qual[r.player]||{p,chips:[]};
            q.chips.push({rank:i+1,l:'#'+(i+1)+' '+pos+' '+l});
          });
        });
      });
      const out=[];
      const cs=CUR_SEASON();
      Object.values(qual).forEach(({p,chips})=>{
        const opp=nextOpp(p.team); if(!opp) return;
        const dr=dvpRank(opp,p.position,'anytimeTd'); if(!dr) return;
        if(dr.rank>10) return;                     // gate: bottom-10 (softest) TD defence for this position
        const logs=allLogs(p.name);
        const sorted=[...logs];                    // already chronological
        const l10=_tdRate(sorted.slice(-10));
        const h2hLogs=sorted.filter(r=>r.opponent===opp);
        const h2h=h2hLogs.length?_tdRate(h2hLogs):null;
        const ssn=_tdRate(sorted.filter(r=>String(r.Year)===String(cs)));
        chips.sort((a,b)=>a.rank-b.rank);
        out.push({p,opp,val:p.totalTds||0,dvpRank:dr.rank,dvpPct:dr.pct,dvpTotal:dr.total,
          chips:chips.slice(0,4),l10,h2h,ssn,season:cs});
      });
      return out.sort((a,b)=>(a.dvpRank-b.dvpRank)||(b.chips.length-a.chips.length));
    }
    function elite(){
      // top 10 in the LEAGUE per stat (not slate); fire when one plays a bottom-5 defence this week
      const pool=(state.idx.players||[]).filter(p=>(p.matches||0)>=4);
      const out=[];
      ELITE_DEFS.forEach(def=>{
        [...pool].sort((a,b)=>(b[def.k]||0)-(a[def.k]||0)).slice(0,10).forEach((p,i)=>{
          const opp=nextOpp(p.team); if(!opp) return;
          const dr=dvpRank(opp,p.position,def.k); if(!dr) return;
          if(dr.rank>5) return;                  // gate: bottom-5 (softest) defence for this stat + position
          out.push({p,def,opp,rank:i+1,val:p[def.k]||0,dvpRank:dr.rank,dvpPct:dr.pct,dvpTotal:dr.total});
        });
      });
      return out.sort((a,b)=>a.dvpRank-b.dvpRank);
    }
    function bunnies(){
      const out=[];
      (state.idx.players||[]).filter(p=>(p.matches||0)>=3).forEach(p=>{
        const opp=nextOpp(p.team); if(!opp) return;
        if(!sameDivision(p.team,opp)) return;                        // divisional rivals only — they meet twice a year
        const byOpp={}; allLogs(p.name).forEach(r=>{ if(r.opponent)(byOpp[r.opponent]=byOpp[r.opponent]||[]).push(r); });
        const vt=byOpp[opp]; if(!vt||vt.length<3) return;            // need >=3 H2H meetings vs the current opp
        BUNNY_STATS.forEach(s=>{
          if((p[s.k]||0)<s.min) return;
          const thisAvg=_avg(vt.map(r=>r[s.k]||0));
          // (1) AVG flavour: averages MORE vs the current opp than vs any other team they've faced
          let best=null,bestOpp=null;
          Object.entries(byOpp).forEach(([o,rs])=>{ if(o===opp||rs.length<2)return; const a=_avg(rs.map(r=>r[s.k]||0)); if(best==null||a>best){best=a;bestOpp=o;} });
          const avgBunny = (best!=null) && (thisAvg>best);   // genuinely their best matchup (not just their only one)
          const diffPct  = (best!=null&&best>0)?((thisAvg-best)/best)*100:null;
          // (2) LINE flavour: cleared the posted line in EVERY H2H meeting (every bunny market posts O/U in NFL)
          const mkt = s.k;
          const posted = mkt?oddsFor(p.name,mkt):null;
          const line = (posted&&posted.line!=null)?posted.line:null;
          const lineBunny = line!=null && vt.every(r=>(r[s.k]||0)>=line);
          if(!avgBunny && !lineBunny) return;
          out.push({p,opp,stat:s,thisAvg,best,bestOpp,diffPct,games:vt.length,avgBunny,lineBunny,line});
        });
      });
      // line-coverage bunnies first, then biggest edge over the field
      return out.sort((a,b)=>(b.lineBunny-a.lineBunny)||((b.diffPct||0)-(a.diffPct||0)));
    }
    function bogey(){
      const out=[];
      (state.idx.players||[]).filter(p=>(p.matches||0)>=3).forEach(p=>{
        const opp=nextOpp(p.team); if(!opp) return;
        if(!sameDivision(p.team,opp)) return;                        // divisional rivals only
        const byOpp={}; allLogs(p.name).forEach(r=>{ if(r.opponent)(byOpp[r.opponent]=byOpp[r.opponent]||[]).push(r); });
        const vt=byOpp[opp]; if(!vt||vt.length<2) return;
        BOGEY_STATS.forEach(s=>{
          if((p[s.k]||0)<s.min) return;
          const thisAvg=_avg(vt.map(r=>r[s.k]||0));
          // (1) AVG flavour: averages LESS vs the current opp than vs any other team
          let worst=null,worstOpp=null;
          Object.entries(byOpp).forEach(([o,rs])=>{ if(o===opp||rs.length<2)return; const a=_avg(rs.map(r=>r[s.k]||0)); if(worst==null||a<worst){worst=a;worstOpp=o;} });
          const avgBogey = (worst==null) || (thisAvg<worst);
          const diffPct  = (worst!=null&&worst>0)?((worst-thisAvg)/worst)*100:null;
          // (2) LINE flavour: NEVER cleared the posted line in any H2H meeting
          const mkt = s.k;
          const posted = mkt?oddsFor(p.name,mkt):null;
          const line = (posted&&posted.line!=null)?posted.line:null;
          const lineBogey = line!=null && vt.every(r=>(r[s.k]||0)<line);
          if(!avgBogey && !lineBogey) return;
          out.push({p,opp,stat:s,thisAvg,worst,worstOpp,diffPct,games:vt.length,avgBogey,lineBogey,line});
        });
      });
      return out.sort((a,b)=>(b.lineBogey-a.lineBogey)||((b.diffPct||0)-(a.diffPct||0)));
    }
    function streakers(){
      const teams=_fixtureSet(); const out=[];
      (state.idx.players||[]).filter(p=>teams.has(p.team)).forEach(p=>{
        const sorted=[...allLogs(p.name)].sort((a,b)=>{ const y=(+a.Year||0)-(+b.Year||0); return y||_roundNum(a.RoundName)-_roundNum(b.RoundName); });
        STREAK_STATS.forEach(([k,l])=>{
          const line=realisticLine2(k,p[k]||0); if(!line) return;
          const vals=sorted.map(r=>r[k]||0); if(vals.length<5) return;
          let streak=0; for(let i=vals.length-1;i>=0;i--){ if(vals[i]>=line)streak++; else break; }
          if(streak<7) return;   // 7 straight ≈ the AFL 10-gate scaled to 17-game seasons
          const opp=nextOpp(p.team);
          const pos=JTTScoring.POS_TO_DVP[p.position]||p.position;
          const dvpPct=opp?JTTScoring.getDVPPct(opp,pos,k):null;
          const runDvp=windowDvp(p,k,sorted.slice(-streak));
          out.push({p,stat:l,statKey:k,line,streak,opp,dvpPct,runDvp,rate:vals.filter(v=>v>=line).length/vals.length});
        });
      });
      return out.sort((a,b)=>b.streak-a.streak);
    }
    function chunkPlays(){
      const teams=_fixtureSet(); const out=[];
      (state.idx.players||[]).filter(p=>teams.has(p.team)&&(p.matches||0)>=3).forEach(p=>{
        const opp=nextOpp(p.team);
        CHUNK_DEFS.forEach(def=>{
          if(!def.pos.includes(p.position)) return;
          const vals=allLogs(p.name).map(r=>r[def.k]).filter(v=>v!=null);
          if(vals.length<CHUNK_MIN_G) return;
          const posted=oddsFor(p.name,def.k);
          const line=(posted&&posted.line!=null)?Math.ceil(posted.line):def.thr;
          const hits=vals.filter(v=>v>=line).length, rate=hits/vals.length;
          if(rate<CHUNK_HIT) return;
          const pos=JTTScoring.POS_TO_DVP[p.position]||p.position;
          const expDvp=opp?JTTScoring.getDVPPct(opp,pos,def.exp):null;
          if(expDvp!=null&&expDvp<=CHUNK_CLAMP) return;   // opp suppresses explosives — history means little
          const longDvp=opp?JTTScoring.getDVPPct(opp,pos,def.k):null;
          const style=_chunkStyle(p,def);
          const sim=_chunkSimilar(p,def,opp,line);
          const avg=_avg(vals);
          const l5=vals.slice(-5).map(v=>Math.round(v)).join(', ');
          const score=rate*100+(expDvp||0)*0.6+(longDvp||0)*0.3+(style?style.pts:0)+(sim?((sim.h/sim.n)-0.5)*30:0);
          out.push({p,opp,def,line,hits,n:vals.length,rate,avg,expDvp,longDvp,style,sim,l5,score,
            posted:!!(posted&&posted.line!=null)});
        });
      });
      return out.sort((a,b)=>b.score-a.score);
    }
    function tackleMachines(){
      const teams=_fixtureSet(); const out=[];
      (state.idx.players||[]).filter(p=>teams.has(p.team)&&DEF_POS.has(p.position)&&(p.matches||0)>=4&&(p.snapPct||0)>=60).forEach(p=>{
        const opp=nextOpp(p.team);
        const vals=allLogs(p.name).map(r=>r.tackles).filter(v=>v!=null&&v>0||v===0).filter(v=>v!=null);
        if(vals.length<WRAP_MIN_G) return;
        const avg=_avg(vals); if(avg<4.5) return;
        const posted=oddsFor(p.name,'tackles');
        const line=(posted&&posted.line!=null)?posted.line:Math.max(4,Math.floor(avg)-1)-0.5;
        const hits=vals.filter(v=>v>line).length, rate=hits/vals.length;
        if(rate<WRAP_HIT) return;
        const funnel=opp?JTTScoring.getDVPPct(opp,p.position,'tackles'):null;
        if(funnel!=null&&funnel<=WRAP_STARVE) return;      // opp starves this position of tackles
        const vol=opp?_oppVolume(opp):null;
        const l5=vals.slice(-5).map(v=>Math.round(v)).join(', ');
        const score=rate*100+(funnel||0)*0.7+(vol&&vol.playsRank<=8?6:0)+(vol&&vol.rushRank<=8&&p.position!=='DB'?6:0)+((p.snapPct||0)>=90?4:0);
        out.push({p,opp,line,hits,n:vals.length,rate,avg,funnel,vol,l5,score,posted:!!(posted&&posted.line!=null)});
      });
      return out.sort((a,b)=>b.score-a.score);
    }
    function usageTrend(){
      const teams=_fixtureSet(); const cs=CUR_SEASON(); const out=[];
      (state.idx.players||[]).filter(p=>teams.has(p.team)&&(p.matches||0)>=USAGE_MIN_G).forEach(p=>{
        const logs=(state.idx.logsByName[p.name]||[]).filter(r=>String(r.Year)===String(cs));
        if(logs.length<USAGE_MIN_G) return;
        const l3=logs.slice(-3), base=logs.slice(0,-3); if(base.length<3) return;
        const av=(a,k)=>a.reduce((s,r)=>s+(r[k]||0),0)/a.length;
        const deltas=[];
        const dSnap=av(l3,'snapPct')-av(base,'snapPct');
        if(Math.abs(dSnap)>=8&&av(l3,'snapPct')>0) deltas.push({k:'snapPct',l:'snaps',d:dSnap,u:'pts'});
        if(['WR','TE','RB'].includes(p.position)){
          const dTs=av(l3,'tgtShare')-av(base,'tgtShare');
          if(Math.abs(dTs)>=4) deltas.push({k:'tgtShare',l:'target share',d:dTs,u:'pts'});
          const dTg=av(l3,'targets')-av(base,'targets');
          if(Math.abs(dTg)>=2) deltas.push({k:'targets',l:'targets/g',d:dTg,u:''});
        }
        if(p.position==='RB'){
          const dRa=av(l3,'rushAtt')-av(base,'rushAtt');
          if(Math.abs(dRa)>=3) deltas.push({k:'rushAtt',l:'carries/g',d:dRa,u:''});
        }
        if(!deltas.length) return;
        const score=deltas.reduce((s,x)=>s+Math.abs(x.d),0);
        const dir=deltas.reduce((s,x)=>s+x.d,0)>=0?'asc':'fade';
        out.push({p,opp:nextOpp(p.team),deltas,dir,score,n:logs.length});
      });
      return out.sort((a,b)=>b.score-a.score);
    }
    function nextManUp(){
      const teams=_fixtureSet(); const out=[];
      const GRP=p=>p==='QB'?'QB':p==='RB'?'RB':(p==='WR'||p==='TE')?'REC':null;
      _outList().forEach(inj=>{
        const star=state.idx.byName[inj.Player]; if(!star) return;
        if(!teams.has(star.team)) return;
        if((star.snapPct||0)<45&&(star.fanPts||0)<8&&!DEF_POS.has(star.position)) return;   // fringe absences don't move volume
        const grp=GRP(star.position)||star.position;
        const withSet=new Set((state.idx.logsByName[star.name]||[]).map(r=>r.MatchId));
        const bens=[];
        playersOnTeam(star.team).forEach(b=>{
          if(b.name===star.name||(b.matches||0)<3) return;
          if((GRP(b.position)||b.position)!==grp) return;
          const logs=(state.idx.logsByName[b.name]||[]).filter(r=>r.Team===star.team);
          const wo=logs.filter(r=>!withSet.has(r.MatchId)), wi=logs.filter(r=>withSet.has(r.MatchId));
          if(wo.length<2||wi.length<3) return;
          const av=(a,k)=>a.reduce((s,r)=>s+(r[k]||0),0)/a.length;
          const keys=grp==='QB'?[['passAtt','att/g'],['passYds','pass yds']]
            :grp==='RB'?[['rushAtt','carries'],['targets','targets']]
            :[['targets','targets'],['tgtShare','tgt share'],['recYds','rec yds']];
          const deltas=keys.map(([k,l])=>({k,l,wo:av(wo,k),wi:av(wi,k),d:av(wo,k)-av(wi,k)})).filter(x=>Math.abs(x.d)>=(x.k==='tgtShare'?2:x.k==='recYds'?8:1));
          if(!deltas.length) return;
          bens.push({b,deltas,nwo:wo.length,gain:deltas.reduce((s,x)=>s+Math.max(0,x.d),0)});
        });
        if(!bens.length) return;
        bens.sort((a,b)=>b.gain-a.gain);
        out.push({star,status:inj.Status,injury:inj.Injury,opp:nextOpp(star.team),bens:bens.slice(0,3),
          gain:bens[0].gain});
      });
      return out.sort((a,b)=>b.gain-a.gain);
    }
    function weatherWatch(){
      const scope=_degenScope; const out=[];
      (state.data.fixture||[]).forEach(g=>{
        if(scope && !scope.has(g.home) && !scope.has(g.away)) return;
        const w=(state.data.weather||[]).find(x=>x.home===g.home&&x.away===g.away); if(!w) return;
        if(w.roof&&w.roof!=='outdoors'&&w.roof!=='open') return;               // dome — no weather angle
        const windy=w.wind!=null&&+w.wind>=WX_WIND;
        const wet=w.code!=null&&+w.code>=61;                                    // WMO: rain and worse
        if(!windy&&!wet) return;
        const headline=(windy?Math.round(w.wind)+' km/h wind':'')+(windy&&wet?' + ':'')+(wet?(w.desc||'rain'):'');
        const runners=[g.home,g.away].map(t=>playersOnTeam(t).filter(p=>p.position==='RB'&&(p.matches||0)>=4)
          .sort((a,b)=>(b.rushAtt||0)-(a.rushAtt||0))[0]).filter(Boolean);
        const deep=[g.home,g.away].flatMap(t=>playersOnTeam(t).filter(p=>(p.aDot||0)>=13&&(p.tgtShare||0)>=15&&(p.matches||0)>=4)).slice(0,3);
        out.push({g,w,headline,windy,wet,runners,deep});
      });
      return out;
    }
    function stackLab(){
      const scope=_degenScope; const out=[];
      (state.data.fixture||[]).forEach(g=>{
        if(scope && !scope.has(g.home) && !scope.has(g.away)) return;
        [g.home,g.away].forEach(team=>{
          const qb=playersOnTeam(team).filter(p=>p.position==='QB'&&(p.matches||0)>=5&&(p.passYds||0)>=180)
            .sort((a,b)=>(b.passYds||0)-(a.passYds||0))[0];
          if(!qb) return;
          const qLine=Math.round(qb.passYds)-20;
          const qp=_stackProb(qb.name,'passYds',qLine);
          const qLeg={p:qb,statKey:'passYds',line:qLine,prob:qp.prob};
          playersOnTeam(team).filter(p=>['WR','TE','RB'].includes(p.position)&&(p.matches||0)>=5&&(p.tgtShare||0)>=15)
            .sort((a,b)=>(b.tgtShare||0)-(a.tgtShare||0)).slice(0,3).forEach(w=>{
            const opts=[];
            if((w.recYds||0)>=40) opts.push(['recYds',Math.round(w.recYds)-8,'Rec Yds']);
            if((w.receptions||0)>=3.5) opts.push(['receptions',Math.max(2,Math.floor(w.receptions)-1),'Receptions']);
            if(!opts.length) return;
            let best=null;
            opts.forEach(([k,line,lab])=>{
              const wp=_stackProb(w.name,k,line);
              const wLeg={p:w,statKey:k,line,prob:wp.prob};
              const rho=_pairRho(qLeg,wLeg);
              const joint=_clusterJoint([qLeg,wLeg]);
              const indep=qLeg.prob*wLeg.prob;
              if(indep<=0) return;
              const uplift=(joint-indep)/indep*100;
              const cand={qb,qLine,w,k,lab,line,rho,joint,indep,uplift,team,opp:nextOpp(team),
                real:qp.n>=6&&wp.n>=6,score:joint*(1+Math.max(0,uplift)/100)};
              if(!best||cand.score>best.score) best=cand;
            });
            if(best&&best.joint>=0.25) out.push(best);
          });
        });
      });
      return out.sort((a,b)=>b.score-a.score);
    }
    function playoffPush(){
      const st=_standings(); if(!st) return [];
      const teams=_fixtureSet(); const out=[];
      Object.entries(st.conf).forEach(([c,arr])=>{
        const seven=arr[6]; if(!seven) return;
        arr.forEach(r=>{
          if(!teams.has(r.team)) return;
          const gb=((seven.w-r.w)+(r.l-seven.l))/2;                     // games back of the 7th seed
          if(!(r.seed>=5&&r.seed<=11&&gb<=2.5)) return;                  // hunting the last wildcard spots
          const opp=nextOpp(r.team);
          const stars=playersOnTeam(r.team).filter(p=>(p.matches||0)>=4&&isPlaying(p.name,r.team))
            .sort((a,b)=>(b.fanPts||0)-(a.fanPts||0)).slice(0,3);
          out.push({team:r.team,conf:c,seed:r.seed,recStr:r.w+'-'+r.l+(r.t?'-'+r.t:''),gb:Math.max(0,gb),
            opp,stars,season:st.season,usedPrev:st.usedPrev});
        });
      });
      return out.sort((a,b)=>(a.gb-b.gb)||(a.seed-b.seed));
    }
    function clampWatch(){
      const dbs=state.data.dbs||[]; if(!dbs.length) return null;    // null => tile shows the "feed pending" state
      const byTeam={}; dbs.forEach(r=>{ if(r&&r.team)(byTeam[r.team]=byTeam[r.team]||[]).push(r); });
      const scope=_degenScope; const out=[];
      (state.data.fixture||[]).forEach(g=>{
        if(scope && !scope.has(g.home) && !scope.has(g.away)) return;
        [[g.home,g.away],[g.away,g.home]].forEach(([myTeam,oppTeam])=>{
          const cbs=byTeam[oppTeam]; if(!cbs||!cbs.length) return;
          playersOnTeam(myTeam).filter(p=>p.position==='WR'&&(p.tgtShare||0)>=20&&isPlaying(p.name,myTeam)).forEach(wr=>{
            cbs.forEach(cb=>out.push({threat:wr,db:cb,oppTeam,type:cb.role||'shadow'}));
          });
        });
      });
      return out;
    }
    function formAlerts(dir){
      const teams=_fixtureSet(); const out=[];
      // [key,label,minimum L3 (spike) / season (drop) to qualify — kills low-volume noise]
      const STATS=[['passYds','Pass Yds',150],['rushYds','Rush Yds',30],['recYds','Rec Yds',30],['receptions','Receptions',3],['rushAtt','Rush Att',8],['fanPts','Fan Pts',8],['tackles','Tackles+Ast',5]];
      (state.idx.players||[]).filter(p=>teams.has(p.team)).forEach(p=>{
        const cur=[...curLogs(p.name)].sort((a,b)=>_roundNum(b.RoundName)-_roundNum(a.RoundName));
        if(cur.length<ALERT_MIN_L3) return;
        const base=cur.length>=10?cur:[...allLogs(p.name)].slice(-10);
        STATS.forEach(([k,l,min])=>{
          const seasonAvg=_avg(base.map(r=>r[k]||0)); if(seasonAvg<=0) return;
          const l3=_avg(cur.slice(0,3).map(r=>r[k]||0));
          const swing=(l3-seasonAvg)/seasonAvg;
          if(Math.abs(swing)<ALERT_THRESHOLD) return;
          const spiking=swing>0;
          if(dir==='spike'&&!spiking) return; if(dir==='drop'&&spiking) return;
          if(spiking && l3<min) return;            // spike must reach a real number
          if(!spiking && seasonAvg<min) return;    // drop only matters off a real baseline
          const opp=nextOpp(p.team);
          const pos=JTTScoring.POS_TO_DVP[p.position]||p.position;
          const thisDvp=opp?JTTScoring.getDVPPct(opp,pos,k):null;
          const runDvp=windowDvp(p,k,cur.slice(0,3));
          out.push({p,stat:l,statKey:k,seasonAvg,l3,swing,spiking,opp,thisDvp,runDvp});
        });
      });
      return out.sort((a,b)=>Math.abs(b.swing)-Math.abs(a.swing));
    }
    function _ledgerCompute(){
      if(_ledgerCache) return _ledgerCache;
      try{ const st=JSON.parse(_LS.getItem('jtt_nfl_ledger')||'null');
        if(st&&st.v===state.dataVersion&&st.rows){ _ledgerCache=st; return st; } }catch(e){}
      const logs=(state.data.gamelogs||[]);
      const posOf=n=>{const p=state.idx.byName[n];return p?p.position:null;};
      // group rows by (year, week) in chronological order
      const byWk={};
      logs.forEach(r=>{ const k=(+r.Year)*100+(+r.Week||0); (byWk[k]=byWk[k]||[]).push(r); });
      const weeks=Object.keys(byWk).map(Number).sort((a,b)=>a-b);
      const hist={};                                     // player -> chronological rows so far
      const funnel={};                                   // (defTeam|pos) -> {sum,g:Set} tackles allowed
      const S={streak:{n:0,h:0,r8:[]},chunk:{n:0,h:0,r8:[]},wrap:{n:0,h:0,r8:[]},tuddy:{n:0,h:0,r8:[]}};
      const tdAllowed={};                                // (defTeam|pos) -> {td,g:Set}
      weeks.forEach(wk=>{
        const rows=byWk[wk];
        const cur=Math.floor(wk/100);
        rows.forEach(r=>{
          const h=hist[r.Player]; if(!h||h.length<6) return;
          const pos=posOf(r.Player); if(!pos) return;
          const grade=(sig,hit)=>{ S[sig].n++; if(hit)S[sig].h++; S[sig].r8.push({wk,hit:hit?1:0}); };
          // Streakers: 7 straight over the realistic line -> continue
          for(const [k] of STREAK_STATS){
            const vals=h.map(x=>x[k]||0);
            const avg=vals.reduce((s,v)=>s+v,0)/vals.length;
            const line=realisticLine2(k,avg); if(line==null) continue;
            let st=0; for(let i=vals.length-1;i>=0;i--){ if(vals[i]>=line)st++; else break; }
            if(st>=7) grade('streak',(r[k]||0)>=line);
          }
          // Chunk: 70%+ clearance history on longest markets
          CHUNK_DEFS.forEach(def=>{
            if(!def.pos.includes(pos)) return;
            const vals=h.map(x=>x[def.k]).filter(v=>v!=null);
            if(vals.length<6||r[def.k]==null) return;
            const rate=vals.filter(v=>v>=def.thr).length/vals.length;
            if(rate>=CHUNK_HIT) grade('chunk',r[def.k]>=def.thr);
          });
          // Tackle Machines: clearance + live funnel gate from running state
          if(DEF_POS.has(pos)){
            const vals=h.map(x=>x.tackles).filter(v=>v!=null);
            if(vals.length>=6){
              const avg=vals.reduce((s,v)=>s+v,0)/vals.length;
              if(avg>=4.5){
                const line=Math.max(4,Math.floor(avg)-1)-0.5;
                const rate=vals.filter(v=>v>line).length/vals.length;
                const f=funnel[r.Opp+'|'+pos];
                const fPct=f&&f.g.size>=4?((f.sum/f.g.size)/_lgAvg(funnel,pos)-1)*100:null;
                if(rate>=WRAP_HIT&&(fPct==null||fPct>WRAP_STARVE)) grade('wrap',(r.tackles||0)>line);
              }
            }
          }
          // Tuddy: TD-rate player into a running bottom-10 TD defence for the position
          if(['RB','WR','TE','QB'].includes(pos)){
            const vals=h.map(x=>x.totalTds||0);
            const tdr=vals.reduce((s,v)=>s+v,0)/vals.length;
            if(tdr>=0.5){
              const ranks=Object.keys(tdAllowed).filter(k2=>k2.endsWith('|'+pos)&&tdAllowed[k2].g.size>=4)
                .map(k2=>({t:k2.split('|')[0],v:tdAllowed[k2].td/tdAllowed[k2].g.size}))
                .sort((a,b)=>b.v-a.v);
              const idx=ranks.findIndex(x=>x.t===r.Opp);
              if(idx>=0&&idx<10) grade('tuddy',(r.totalTds||0)>=1);
            }
          }
        });
        // absorb this week into running state AFTER grading
        rows.forEach(r=>{
          (hist[r.Player]=hist[r.Player]||[]).push(r);
          const pos=posOf(r.Player); if(!pos) return;
          if(DEF_POS.has(pos)&&r.tackles!=null){
            const f=funnel[r.Opp+'|'+pos]=funnel[r.Opp+'|'+pos]||{sum:0,g:new Set()};
            f.sum+=r.tackles; f.g.add(r.MatchId);
          }
          if(['RB','WR','TE','QB'].includes(pos)){
            const t=tdAllowed[r.Opp+'|'+pos]=tdAllowed[r.Opp+'|'+pos]||{td:0,g:new Set()};
            t.td+=(r.totalTds||0); t.g.add(r.MatchId);
          }
        });
      });
      const lastWk=weeks[weeks.length-1]||0;
      const rowsOut=Object.entries(S).map(([k,v])=>{
        const l8=v.r8.filter(x=>x.wk>lastWk-8);
        return {sig:k,n:v.n,h:v.h,pct:v.n?v.h/v.n*100:0,
          n8:l8.length,h8:l8.reduce((s,x)=>s+x.hit,0)};
      });
      const res={v:state.dataVersion,rows:rowsOut,weeks:weeks.length};
      try{ _LS.setItem('jtt_nfl_ledger',JSON.stringify(res)); }catch(e){}
      _ledgerCache=res; return res;
    }


    /* ========================================================================
     * RENDER LAYER (NFL)
     * ------------------------------------------------------------------------
     * abbr / posShort / degRow / degWrap / bookName / _degBadges / elitePage /
     * snagsBody / SNAG_POS also exist in the shell with DIFFERENT bodies — the
     * shell's are AFL-shaped (degRow takes an extra `card` arg, posShort maps
     * Midfielder/Ruck, abbr maps full club names to 3 letters). They are
     * re-declared here so NFL rendering stays self-consistent and cannot be
     * broken by an AFL-side edit. Verified by build/difftrap.py.
     *
     * Render helpers that ARE byte-identical across sports (_byGame, _fc,
     * emptyState, esc, fmt, teamLogo, lineTag, multiLineTag, muTag, underTag,
     * streakOdds, formNote, _injuryFor) are injected instead of duplicated.
     * ==================================================================== */
    const SNAG_POS=['All','RB','WR','TE','QB'];
    const LEDGER_LABELS={streak:['Streakers','7-game streaks continuing at the realistic line'],
      chunk:['Chunk Plays','longest rec/rush/comp clearing the market-typical gate'],
      wrap:['Tackle Machines','tackles+assists clearing the est line, funnel-gated'],
      tuddy:['Tuddy Targets','TD-rate players scoring v a running bottom-10 TD defence']};

    const POS_C = {'QB':'#ef4444','RB':'#22c55e','WR':'#3b82f6','TE':'#f59e0b','LB':'#a855f7','DL':'#f97316','DB':'#06b6d4'};
    const ALL_BOOKS=[['sportsbet','Sportsbet'],['ladbrokes_au','Ladbrokes'],['tab','TAB'],['pointsbetau','Pointsbet'],['betr_au','Betr'],['dabble_au','Dabble'],['betright','BetRight'],['unibet','Unibet'],['betfair_ex_au','Betfair']];
    const _BOOK_NAMES=Object.fromEntries(ALL_BOOKS);
    function abbr(t){return t||'';}
    function posShort(p){return p||'';}
    function bookName(k){ return k?(_BOOK_NAMES[k]||(k.charAt(0).toUpperCase()+k.slice(1))):''; }
    function _bookShort(k){ return ({sportsbet:'SB',tab:'TAB',pointsbet:'PB',ladbrokes:'LAD',neds:'NED',betr:'BTR',dabble:'DAB',betright:'BR',unibet:'UNI',bet365:'365',bluebet:'BLU',betfair:'BF'})[k]||(k||'').slice(0,3).toUpperCase(); }
    function _degRight(right,hl){
      if(right&&typeof right==='object'){
        return '<div class="deg-v2"><div class="deg-v2a">'+hl(right.v1)+(right.l1?'<span>'+right.l1+'</span>':'')+'</div>'+
          (right.v2!=null&&right.v2!==''?'<div class="deg-v2b">'+hl(right.v2)+(right.l2?'<span>'+right.l2+'</span>':'')+'</div>':'')+'</div>';
      }
      return hl(right);
    }
    function _warnBadge(name){
      const neg=_negFlags()[name]; if(!neg||!neg.length) return '';
      const tip='<b>Also flagged</b>'+neg.map(n=>'<br>\u2022 '+n.label+(n.detail?' \u2014 '+n.detail:'')).join('');
      return '<span class="deg-warn" data-tip="'+_attrEsc(tip)+'" title="'+_attrEsc(neg.map(n=>n.label).join(' \u00b7 '))+'" onclick="_negTip(event,this)" onmouseenter="_negShow(this)" onmouseleave="_negHide()">!</span>';
    }
    function degRow(name,col,right,sub,onclickName){
      const hl=s=>(''+s).replace(/(\$\d+(?:\.\d+)?)/g,'<b class="deg-odds">$1</b>');
      return '<div class="fixture-row deg-row" style="cursor:pointer" onclick="openPlayer(\''+esc(onclickName||name).replace(/\x27/g,"\\\x27")+'\')">'+
        '<div class="deg-main"><div class="fxr-name deg-nm"><span>'+name+'</span>'+_degBadges(name)+'</div><div class="deg-sub">'+hl(sub)+'</div></div>'+
        '<div class="deg-val" style="color:'+col+'">'+_degRight(right,hl)+'</div></div>';
    }
    function degWrap(icon,title,items,colorClass){
      if(!items) return _degenFocused?'':emptyState(icon,title+' — no data','Needs game logs + this week\u2019s fixture.');
      if(!items.length) return _degenFocused?'':emptyState(icon,'No '+title.toLowerCase()+' this week','Nothing cleared the gate on the current slate.');
      return '<details class="tp-collapse '+(colorClass||'')+'" open><summary class="tp-collapse-sum"><i class="ti '+icon+'"></i><span class="tp-collapse-title">'+title+'</span><i class="ti ti-chevron-down tp-collapse-chev"></i></summary><div class="tp-collapse-body">'+items.join('')+'</div></details>';
    }
    let _negCache=null,_negKey=null;
    function _negFlags(){
      const key=(state.dataVersion||'')+'|'+((state.idx.oddsMeta&&state.idx.oddsMeta.updated)||'')+'|'+((state.data.fixture||[]).length);
      if(_negCache&&_negKey===key) return _negCache;
      const m={};
      const add=(name,type,label,detail)=>{ if(!name)return; const a=(m[name]=m[name]||[]); if(!a.some(x=>x.type===type)) a.push({type,label,detail}); };
      try{ bogey().forEach(b=>add(b.p.name,'bogey','Bogey',(b.opp?'quiet v '+abbr(b.opp):'quiet v opp')+(b.stat?' ('+b.stat.l+')':''))); }catch(e){}
      try{ formAlerts('drop').forEach(fa=>add(fa.p.name,'cool','Form cooling',fa.stat+' L3 down '+Math.round(Math.abs(fa.swing)*100)+'%')); }catch(e){}
      try{ const cw=clampWatch(); if(cw) cw.forEach(t=>add(t.threat.name,'clamp','Clamp risk','likely '+((t.db&&t.db.player)||'a shadow CB')+' ('+abbr(t.oppTeam)+')')); }catch(e){}
      _negCache=m; _negKey=key; return m;
    }
    function _degBadges(name){
      let h='';
      const p=state.idx&&state.idx.byName?state.idx.byName[name]:null;
      if(p&&p.position){ const col=POS_C[p.position]||'#888'; h+='<span class="deg-pos" style="color:'+col+';border-color:'+col+'55;background:'+col+'1a">'+posShort(p.position)+'</span>'; }
      const lu=(state.data.lineups||[]).find(r=>r.player===name && r.depth===1);
      if(lu) h+='<span class="deg-named">STARTER</span>';
      const ij=_injuryFor(name); if(ij) h+='<span class="deg-inj" title="'+esc((ij.Injury||'Injury')+(ij.Returning?' \u00b7 '+ij.Returning:''))+'">OUT</span>';
      h+=_warnBadge(name);
      return h;
    }
    function _rzPos(name){ const p=state.idx.byName[name]; return p?p.position:null; }
    function _tuddyMini(label,rows,key,isPct){
      const top=rows.filter(r=>(r[key]||0)>0).sort((a,b)=>(b[key]||0)-(a[key]||0)).slice(0,5);
      if(!top.length) return '';
      const body=top.map(r=>{
        const q=esc(r.player).replace(/\x27/g,"\\\x27");
        return '<div class="sp-row" onclick="openPlayer(\''+q+'\')">'+teamLogo(r.team,16)+
          '<span class="sp-nm" style="margin-left:6px">'+esc(r.player)+'</span>'+
          '<span class="sp-av">'+(isPct?(+r[key]).toFixed(1)+'%':r[key])+'</span></div>';
      }).join('');
      return '<div><div class="mini-th">'+label+'</div>'+body+'</div>';
    }
    function _tuddyGroup(title,rows,cols){
      const minis=cols.map(([l,k,pct])=>_tuddyMini(l,rows,k,pct)).filter(Boolean);
      if(!minis.length) return '';
      return '<div class="section-title">'+title+'</div>'+
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin-bottom:10px">'+minis.join('')+'</div>';
    }
    function tuddyBoards(posF){
      const rz=(state.data.redzone||[]);
      if(!rz.length) return '<div class="tp-body-meta" style="border:0;margin:10px 0">Redzone splits land with the next data build (redzone.json).</div>';
      const byPos={}; rz.forEach(r=>{ const p=_rzPos(r.player); if(p)(byPos[p]=byPos[p]||[]).push(r); });
      const want=p=>posF==='All'||posF===p;
      let h='';
      if(want('WR')){
        h+=_tuddyGroup('WR Redzone',byPos.WR||[],[['Targets','rzTgt'],['RZ Target %','rzTgtPct',1],['Receptions','rzRec'],['Touchdowns','rzRecTd']]);
        h+=_tuddyGroup('WR Inside 10',byPos.WR||[],[['Targets','i10Tgt'],['Target %','i10TgtPct',1],['Receptions','i10Rec'],['Touchdowns','i10RecTd']]);
      }
      if(want('TE')){
        h+=_tuddyGroup('TE Redzone',byPos.TE||[],[['Targets','rzTgt'],['RZ Target %','rzTgtPct',1],['Receptions','rzRec'],['Touchdowns','rzRecTd']]);
      }
      if(want('RB')){
        h+=_tuddyGroup('RB Redzone — receiving',byPos.RB||[],[['Targets','rzTgt'],['RZ Target %','rzTgtPct',1],['Receptions','rzRec'],['Touchdowns','rzRecTd']]);
        h+=_tuddyGroup('RB Redzone — rushing',byPos.RB||[],[['Attempts','rzAtt'],['Rush %','rzRushPct',1],['Touchdowns','rzRushTd']]);
        h+=_tuddyGroup('RB Inside 10',byPos.RB||[],[['Attempts','i10Att'],['Rush %','i10RushPct',1],['Touchdowns','i10RushTd']]);
        h+=_tuddyGroup('RB Inside 5',byPos.RB||[],[['Attempts','i5Att'],['Rush %','i5RushPct',1],['Touchdowns','i5RushTd']]);
      }
      if(want('QB')){
        h+=_tuddyGroup('QB Redzone — rushing',byPos.QB||[],[['Attempts','rzAtt'],['Rush %','rzRushPct',1],['Touchdowns','rzRushTd']]);
      }
      return h;
    }
    function mostTdsAllowed(){
      const rows=state.data.dvp||[]; if(!rows.length) return '';
      const POSN=['WR','TE','RB','QB'];
      const ranked={};
      POSN.forEach(pos=>{ ranked[pos]=rows.filter(r=>r.pos===pos&&r.anytimeTd!=null)
        .sort((a,b)=>b.anytimeTd-a.anytimeTd).slice(0,10); });
      if(!POSN.some(p=>ranked[p].length)) return '';
      let h='<div class="section-title">Most Touchdowns Allowed · per game, by position</div>';
      h+='<div class="stbl-wrap"><table class="stbl bx"><thead><tr><th>#</th>'+POSN.map(p=>'<th>'+p+'</th>').join('')+'</tr></thead><tbody>';
      for(let i=0;i<10;i++){
        h+='<tr><td>'+(i+1)+'</td>'+POSN.map(pos=>{
          const r=ranked[pos][i]; if(!r) return '<td>—</td>';
          return '<td>'+teamLogo(r.team,16)+' '+abbr(r.team)+' <span style="color:var(--text-3);font-size:10px">'+(+r.anytimeTd).toFixed(2)+'</span></td>';
        }).join('')+'</tr>';
      }
      return h+'</tbody></table></div>';
    }
    function firstTdSection(posOk){
      const cs=CUR_SEASON();
      const teams=_fixtureSet();
      const cnt={}, games={};
      (state.data.firsttd||[]).forEach(r=>{
        if(String(r.season)!==String(cs)) return;
        if(!r.gameFirst||!r.player) return;
        cnt[r.player]=(cnt[r.player]||0)+1;
      });
      const rows=Object.entries(cnt).map(([name,n])=>{
        const p=state.idx.byName[name]; if(!p||!teams.has(p.team)||!posOk(p)) return null;
        return {p,n};
      }).filter(Boolean).sort((a,b)=>b.n-a.n).slice(0,10);
      if(!rows.length) return '';
      let h='<div class="section-title">First TD scorers · '+cs+' season</div><div class="stbl-wrap"><table class="stbl bx"><thead><tr><th>#</th><th>Player</th><th>Tm</th><th>Game 1st TDs</th><th>Odds</th></tr></thead><tbody>';
      rows.forEach((r,i)=>{
        const od=oddsFor(r.p.name,'firstTd');
        h+='<tr onclick="openPlayer(\''+esc(r.p.name).replace(/\x27/g,"\\\x27")+'\')" style="cursor:pointer"><td>'+(i+1)+'</td><td class="name">'+esc(r.p.name)+'</td><td>'+abbr(r.p.team)+'</td><td>'+r.n+'</td><td>'+(od&&od.over!=null?'$'+od.over.toFixed(2)+(od.book?' '+_bookShort(od.book):''):'—')+'</td></tr>';
      });
      return h+'</tbody></table></div>';
    }
    function tdOddsTag(name){
      const any=oddsFor(name,'anytimeTd'), fst=oddsFor(name,'firstTd');
      if(any&&any.over!=null&&any.book) return ' · ATD $'+any.over.toFixed(2)+' ('+bookName(any.book)+')';
      if(fst&&fst.over!=null&&fst.book) return ' · 1st TD $'+fst.over.toFixed(2)+' ('+bookName(fst.book)+')';
      return '';
    }
    function tuddyCard(c){
      const q=esc(c.p.name).replace(/\x27/g,"\\\x27");
      const rate=(r,lab)=>r?('<span><b>'+lab+'</b> '+r.h+'/'+r.n+'</span>'):'';
      const rates=[rate(c.l10,'L10'),rate(c.h2h,'H2H'),rate(c.ssn,c.season)].filter(Boolean).join(' · ');
      const chips=['<span class="lu-p" style="color:#f59e0b;border-color:#f59e0b55">#'+c.dvpRank+' TDs allowed '+posShort(c.p.position)+'</span>']
        .concat(c.chips.map(ch=>'<span class="lu-p">'+esc(ch.l)+'</span>')).join(' ');
      const od=tdOddsTag(c.p.name);
      return '<div class="lc-card" onclick="openPlayer(\''+q+'\')">'+
        '<div class="lc-hd"><span class="lc-nm">'+esc(c.p.name)+'</span>'+_degBadges(c.p.name)+
          '<span class="lc-meta">'+posShort(c.p.position)+' · '+abbr(c.p.team)+' v '+abbr(c.opp)+(od?' ·'+od:'')+'</span></div>'+
        (rates?'<div class="tp-body-meta" style="border:0;padding:2px 0 6px">TD games: '+rates+'</div>':'')+
        '<div class="lu-grid" style="gap:5px">'+chips+'</div>'+
      '</div>';
    }
    function snagsHot(posOk){ return snags().filter(s=>posOk(s.p)); }
    function snagsBody(){
      const posF=state.view.snagPos||'All';
      const posOk=p=>posF==='All'||p.position===posF;
      const hot=snagsHot(posOk);
      let h='';
      if(hot.length){
        h+='<div class="section-title">Hot Tuddies · top-5 in a redzone stat v bottom-10 TD defence</div>'+
          _byGame(hot, s=>s.p.team, tuddyCard).join('');
      } else if(!(state.data.redzone||[]).length){
        h+='<div class="tp-body-meta" style="border:0;margin:8px 0">Hot Tuddies arm once redzone.json lands with the next data build.</div>';
      } else {
        h+='<div class="tp-body-meta" style="border:0;margin:8px 0">No redzone top-5'+(posF!=='All'?' ('+posShort(posF)+')':'')+' faces a bottom-10 TD defence this week.</div>';
      }
      h+=mostTdsAllowed();
      h+=tuddyBoards(posF);
      h+=firstTdSection(posOk);
      return h;
    }
    function _snagPosBar(posF){ return SNAG_POS.map(p=>'<button class="snag-pos'+(posF===p?' on':'')+'" onclick="setSnagPos(\''+p+'\')">'+(p==='All'?'All':posShort(p))+'</button>').join(''); }
    function snagsPage(){
      const posF=state.view.snagPos||'All';
      return '<div class="snag-posbar">'+_snagPosBar(posF)+'</div><div id="snags-body">'+snagsBody()+'</div>';
    }
    function elitePage(){
      const sig=elite();
      let h='';
      if(sig.length){
        h+='<div class="section-title">Hot Elite Matchups · top-10 v bottom-5 defence</div>'+
          _byGame(sig.slice(0,20), s=>s.p.team, s=>degRow(s.p.name,'#22c55e',{v1:fmt(s.val,1),l1:s.def.l,v2:'#'+s.dvpRank,l2:'softest'},
            posShort(s.p.position)+' · '+abbr(s.p.team)+' v '+abbr(s.opp)+' · '+fmt(s.val,1)+' '+s.def.l.toLowerCase()+multiLineTag(s.p.name,s.def.k))).join('');
      } else {
        h+='<div class="tp-body-meta" style="border:0;margin:8px 0">No top-10 player faces a bottom-5 defence this week.</div>';
      }
      const pool=(state.idx.players||[]).filter(p=>(p.matches||0)>=4);
      const teamsArr=Object.values(state.idx.teamMap||{});
      ELITE_DEFS.forEach(def=>{
        const k=def.k, ak=k+'_a';
        const withA=teamsArr.filter(t=>t[ak]!=null);
        const lg=withA.length?withA.reduce((s,t)=>s+(+t[ak]),0)/withA.length:0;
        const easiest=[...withA].sort((a,b)=>(+b[ak])-(+a[ak])).slice(0,5);
        const toughest=[...withA].sort((a,b)=>(+a[ak])-(+b[ak])).slice(0,5);
        const top10=[...pool].sort((a,b)=>(b[k]||0)-(a[k]||0)).slice(0,10);
        const prows=top10.map((p,i)=>{
          const opp=nextOpp(p.team), dr=opp?dvpRank(opp,p.position,k):null, mi=dr?JTTScoring.muInfo(dr.pct):null;
          const mu=dr?'<span style="color:'+(mi?mi.c:'#888')+'">'+abbr(opp)+' #'+dr.rank+'/'+dr.total+'</span>':'—';
          return '<tr onclick="openPlayer(\''+esc(p.name).replace(/\x27/g,"\\\x27")+'\')" style="cursor:pointer"><td>'+(i+1)+'</td><td class="name">'+esc(p.name)+'</td><td>'+abbr(p.team)+'</td><td>'+fmt(p[k],1)+'</td><td>'+mu+'</td></tr>';
        }).join('');
        const trows=(arr,col)=>arr.map((t,i)=>{const pct=lg?((+t[ak]-lg)/lg*100):0;return '<tr><td>'+(i+1)+'</td><td class="name">'+abbr(t.team)+'</td><td>'+fmt(t[ak],1)+'</td><td style="color:'+col+'">'+(pct>0?'+':'')+pct.toFixed(0)+'%</td></tr>';}).join('');
        h+='<details class="tp-collapse c-green"><summary class="tp-collapse-sum"><i class="ti ti-trophy"></i><span class="tp-collapse-title">'+def.l+'</span><i class="ti ti-chevron-down tp-collapse-chev"></i></summary><div class="tp-collapse-body">'+
          '<div class="stbl-wrap"><table class="stbl bx"><thead><tr><th>#</th><th>Player</th><th>Tm</th><th>Avg</th><th>Matchup</th></tr></thead><tbody>'+prows+'</tbody></table></div>'+
          '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px">'+
          '<div><div class="section-title" style="color:#22c55e">5 Easiest</div><div class="stbl-wrap"><table class="stbl bx"><thead><tr><th>#</th><th>Team</th><th>Alw</th><th>vs Lg</th></tr></thead><tbody>'+trows(easiest,'#22c55e')+'</tbody></table></div></div>'+
          '<div><div class="section-title" style="color:#ef4444">5 Toughest</div><div class="stbl-wrap"><table class="stbl bx"><thead><tr><th>#</th><th>Team</th><th>Alw</th><th>vs Lg</th></tr></thead><tbody>'+trows(toughest,'#ef4444')+'</tbody></table></div></div></div>'+
          '</div></details>';
      });
      return h;
    }
    function chunkCard(c){
      const q=esc(c.p.name).replace(/\x27/g,"\\\x27");
      const od=lineTag(c.p.name,c.def.k);
      const chips=[];
      if(c.expDvp!=null){
        const col=c.expDvp>=10?'#22c55e':c.expDvp>=3?'#86efac':c.expDvp<=-6?'#f97316':'var(--text-3)';
        chips.push('<span class="lu-p" style="color:'+col+';border-color:'+col+'55">'+abbr(c.opp)+' '+(c.expDvp>=0?'allows +':'allows ')+c.expDvp.toFixed(0)+'% '+c.def.expL+' to '+posShort(c.p.position)+'s</span>');
      }
      if(c.longDvp!=null){
        const col=c.longDvp>=8?'#22c55e':c.longDvp<=-8?'#f97316':'var(--text-3)';
        chips.push('<span class="lu-p" style="color:'+col+';border-color:'+col+'55">longest allowed '+(c.longDvp>=0?'+':'')+c.longDvp.toFixed(0)+'% v league</span>');
      }
      if(c.style) chips.push('<span class="lu-p" style="color:'+c.style.c+';border-color:'+c.style.c+'55">'+esc(c.style.txt)+'</span>');
      if(c.sim){
        const sr=c.sim.h/c.sim.n, col=sr>=0.6?'#22c55e':sr>=0.4?'#eab308':'#ef4444';
        chips.push('<span class="lu-p" style="color:'+col+';border-color:'+col+'55">similar '+posShort(c.p.position)+'s '+c.sim.h+'/'+c.sim.n+' cleared v '+abbr(c.opp)+' ('+c.sim.used+' players)</span>');
      }
      return '<div class="lc-card" onclick="openPlayer(\''+q+'\')">'+
        '<div class="lc-hd"><span class="lc-nm">'+esc(c.p.name)+'</span>'+_degBadges(c.p.name)+
          '<span class="lc-meta">'+posShort(c.p.position)+' · '+abbr(c.p.team)+(c.opp?' v '+abbr(c.opp):'')+(od?' ·'+od:'')+'</span></div>'+
        '<div class="tp-body-meta" style="border:0;padding:2px 0 6px"><b>'+c.def.l+' '+c.line+'+</b>'+(c.posted?'':' (est line)')+' · hit '+c.hits+'/'+c.n+' ('+Math.round(c.rate*100)+'%) · avg '+c.avg.toFixed(0)+' · L5 '+c.l5+'</div>'+
        (chips.length?'<div class="lu-grid" style="gap:5px">'+chips.join(' ')+'</div>':'')+
      '</div>';
    }
    function tackleCard(c){
      const q=esc(c.p.name).replace(/\x27/g,"\\\x27");
      const od=lineTag(c.p.name,'tackles');
      const chips=[];
      if(c.funnel!=null){
        const col=c.funnel>=10?'#22c55e':c.funnel>=3?'#86efac':c.funnel<=-6?'#f97316':'var(--text-3)';
        chips.push('<span class="lu-p" style="color:'+col+';border-color:'+col+'55">'+abbr(c.opp)+' offence feeds '+(c.funnel>=0?'+':'')+c.funnel.toFixed(0)+'% '+posShort(c.p.position)+' tackles</span>');
      }
      if(c.vol){
        chips.push('<span class="lu-p">'+c.vol.plays.toFixed(0)+' plays/g (#'+c.vol.playsRank+'/'+c.vol.n+')</span>');
        if(c.p.position!=='DB') chips.push('<span class="lu-p">'+c.vol.rushAtt.toFixed(0)+' rush att/g (#'+c.vol.rushRank+')</span>');
      }
      if((c.p.snapPct||0)>=90) chips.push('<span class="lu-p" style="color:#22c55e;border-color:#22c55e55">every-down · '+c.p.snapPct.toFixed(0)+'% snaps</span>');
      return '<div class="lc-card" onclick="openPlayer(\''+q+'\')">'+
        '<div class="lc-hd"><span class="lc-nm">'+esc(c.p.name)+'</span>'+_degBadges(c.p.name)+
          '<span class="lc-meta">'+posShort(c.p.position)+' · '+abbr(c.p.team)+(c.opp?' v '+abbr(c.opp):'')+(od?' ·'+od:'')+'</span></div>'+
        '<div class="tp-body-meta" style="border:0;padding:2px 0 6px"><b>Tackles+Ast '+c.line+'+</b>'+(c.posted?'':' (est line)')+' · hit '+c.hits+'/'+c.n+' ('+Math.round(c.rate*100)+'%) · avg '+c.avg.toFixed(1)+' · L5 '+c.l5+'</div>'+
        (chips.length?'<div class="lu-grid" style="gap:5px">'+chips.join(' ')+'</div>':'')+
      '</div>';
    }
    function stackCard(c){
      const q1=esc(c.qb.name).replace(/\x27/g,"\\\x27"), q2=esc(c.w.name).replace(/\x27/g,"\\\x27");
      const upCol=c.uplift>=8?'#22c55e':c.uplift<=-5?'#f97316':'var(--text-3)';
      return '<div class="lc-card">'+
        '<div class="lc-hd"><span class="lc-nm"><i class="ti ti-link"></i> '+abbr(c.team)+' stack</span>'+
          '<span class="lc-meta">'+(c.opp?'v '+abbr(c.opp)+' · ':'')+'fair $'+(1/c.joint).toFixed(2)+'</span></div>'+
        '<div class="dv-leg" style="cursor:pointer" onclick="openPlayer(\''+q1+'\')"><span class="dv-n">'+esc(c.qb.name)+'</span><span class="dv-l">'+c.qLine+'+ Pass Yds</span></div>'+
        '<div class="dv-leg" style="cursor:pointer" onclick="openPlayer(\''+q2+'\')"><span class="dv-n">'+esc(c.w.name)+'</span><span class="dv-l">'+c.line+'+ '+c.lab+'</span></div>'+
        '<div class="lu-grid" style="gap:5px;margin-top:6px">'+
          '<span class="lu-p" style="color:'+upCol+';border-color:'+upCol+'55">correlated '+Math.round(c.joint*100)+'% v naive '+Math.round(c.indep*100)+'% ('+(c.uplift>=0?'+':'')+c.uplift.toFixed(0)+'%)</span>'+
          '<span class="lu-p">\u03c1 '+c.rho.toFixed(2)+'</span>'+
          (c.real?'':'<span class="lu-p" style="color:#eab308;border-color:#eab30855">est · thin shared history</span>')+
        '</div></div>';
    }
    function usageCard(c){
      const q=esc(c.p.name).replace(/\x27/g,"\\\x27");
      const col=c.dir==='asc'?'#22c55e':'#f97316';
      const chips=c.deltas.map(x=>'<span class="lu-p" style="color:'+(x.d>=0?'#22c55e':'#f97316')+'">'+
        (x.d>=0?'+':'')+x.d.toFixed(1)+(x.u?' '+x.u:'')+' '+x.l+' (L3 v season)</span>').join(' ');
      return '<div class="lc-card" onclick="openPlayer(\''+q+'\')">'+
        '<div class="lc-hd"><span class="lc-nm">'+esc(c.p.name)+'</span>'+_degBadges(c.p.name)+
          '<span class="lc-meta" style="color:'+col+'">'+(c.dir==='asc'?'ASCENDING':'FADING')+' · '+posShort(c.p.position)+' · '+abbr(c.p.team)+(c.opp?' v '+abbr(c.opp):'')+'</span></div>'+
        '<div class="lu-grid" style="gap:5px">'+chips+'</div></div>';
    }
    function nextCard(c){
      const rows=c.bens.map(x=>{
        const q=esc(x.b.name).replace(/\x27/g,"\\\x27");
        const chips=x.deltas.map(d=>'<span class="lu-p" style="color:'+(d.d>=0?'#22c55e':'#f97316')+'">'+
          (d.d>=0?'+':'')+d.d.toFixed(1)+' '+d.l+' without ('+d.wo.toFixed(1)+' v '+d.wi.toFixed(1)+')</span>').join(' ');
        return '<div style="padding:6px 0;border-top:1px solid var(--line);cursor:pointer" onclick="openPlayer(\''+q+'\')">'+
          '<div style="font-weight:700;font-size:12px;margin-bottom:4px">'+esc(x.b.name)+' <span style="color:var(--text-3);font-weight:400">'+posShort(x.b.position)+' · n='+x.nwo+' without</span></div>'+
          '<div class="lu-grid" style="gap:4px">'+chips+'</div></div>';
      }).join('');
      return '<div class="lc-card"><div class="lc-hd"><span class="lc-nm"><i class="ti ti-user-off"></i> '+esc(c.star.name)+' '+esc(String(c.status||'OUT').toUpperCase())+'</span>'+
        '<span class="lc-meta">'+posShort(c.star.position)+' · '+abbr(c.star.team)+(c.opp?' v '+abbr(c.opp):'')+(c.injury?' · '+esc(c.injury):'')+'</span></div>'+rows+'</div>';
    }
    function wxCard(x){
      const chips=[];
      x.runners.forEach(r=>{ const q=esc(r.name).replace(/\x27/g,"\\\x27");
        chips.push('<span class="lu-p" style="color:#22c55e;border-color:#22c55e55;cursor:pointer" onclick="openPlayer(\''+q+'\')">rush funnel · '+esc(r.name)+' '+fmt(r.rushAtt,1)+' att/g</span>'); });
      x.deep.forEach(p=>{ const q=esc(p.name).replace(/\x27/g,"\\\x27");
        chips.push('<span class="lu-p" style="color:#f97316;border-color:#f9731655;cursor:pointer" onclick="openPlayer(\''+q+'\')">fade deep · '+esc(p.name)+' aDot '+(p.aDot||0).toFixed(1)+'</span>'); });
      if(x.windy) chips.push('<span class="lu-p">unders lean · deep passing degrades in '+Math.round(x.w.wind)+' km/h</span>');
      return '<div class="lc-card"><div class="lc-hd"><span class="lc-nm"><i class="ti ti-'+(x.wet?'cloud-rain':'wind')+'"></i> '+abbr(x.g.home)+' v '+abbr(x.g.away)+'</span>'+
        '<span class="lc-meta">'+esc(x.headline)+(x.g.venue?' · '+esc(x.g.venue):'')+'</span></div>'+
        (chips.length?'<div class="lu-grid" style="gap:5px">'+chips.join(' ')+'</div>':'')+'</div>';
    }
    function pushCard(c){
      const gbStr=c.gb===0?'holds the 7 seed line':(c.gb+' GB of the 7 seed');
      const starRows=c.stars.map(p=>{
        const mk=p.position==='QB'?'passYds':p.position==='RB'?'rushYds':'recYds';
        const pct=c.opp?JTTScoring.getDVPPct(c.opp,JTTScoring.POS_TO_DVP[p.position]||p.position,mk):null;
        const q=esc(p.name).replace(/\x27/g,"\\\x27");
        return '<div class="dv-leg" style="cursor:pointer" onclick="openPlayer(\''+q+'\')"><span class="dv-n">'+esc(p.name)+'</span>'+
          '<span class="dv-l">'+posShort(p.position)+' · '+fmt(p.fanPts,1)+' FP'+(pct!=null?' · dvp '+(pct>0?'+':'')+pct.toFixed(0)+'%':'')+'</span>'+
          '<span class="dv-h">'+(tdOddsTag(p.name).replace(/^ \u00b7 /,'')||'')+'</span></div>';
      }).join('');
      return '<div class="lc-card">'+
        '<div class="lc-hd"><span class="lc-nm">'+teamLogo(c.team,20)+' '+abbr(c.team)+'</span>'+
          '<span class="lc-meta">'+esc(c.conf)+' #'+c.seed+' · '+c.recStr+' · '+gbStr+(c.opp?' · v '+abbr(c.opp):'')+(c.usedPrev?' · '+c.season+' standings':'')+'</span></div>'+
        starRows+'</div>';
    }
    function ledgerPage(){
      const L=_ledgerCompute();
      if(!L||!L.rows||!L.rows.some(r=>r.n)) return emptyState('ti-notebook','Ledger needs history','Signal grading builds from completed game weeks.');
      let h='<div class="tp-body-meta" style="border:0;margin-bottom:10px">Every signal condition replayed chronologically over '+L.weeks+' data weeks — evaluated with only the history available at the time, graded against what actually happened. Hit rates are at <b>estimated lines</b>; posted-line grading, CLV and units land with the live odds feed.</div>';
      h+='<div class="stbl-wrap"><table class="stbl bx"><thead><tr><th>Signal</th><th>Graded</th><th>Hit</th><th>Hit %</th><th>Last 8 wks</th></tr></thead><tbody>';
      L.rows.sort((a,b)=>b.pct-a.pct).forEach(r=>{
        const [nm,desc]=LEDGER_LABELS[r.sig]||[r.sig,''];
        const col=r.pct>=62?'#22c55e':r.pct>=52?'#eab308':'#ef4444';
        h+='<tr><td class="name" title="'+esc(desc)+'">'+esc(nm)+'</td><td>'+r.n+'</td><td>'+r.h+'</td>'+
          '<td style="color:'+col+';font-weight:700">'+r.pct.toFixed(1)+'%</td>'+
          '<td>'+(r.n8?(r.h8+'/'+r.n8+' ('+Math.round(r.h8/r.n8*100)+'%)'):'—')+'</td></tr>';
      });
      h+='</tbody></table></div>';
      h+='<div class="tp-body-meta" style="border:0;margin-top:8px">Method notes: no look-ahead — a Week 9 signal only knew Weeks 1–8. Chunk and Tackle grades use the same qualification gates as the live tiles; Tuddy uses the TD-rate × soft-defence core (redzone ranks aren\u2019t reconstructable historically). Streakers grade continuation of the streak.</div>';
      return h;
    }
    function renderTile(k){
      const M=v=>fmt(v,1), abv=t=>abbr(t);
      if(k==='paydirt'){
        const rows=_byGame(_fc(snags(),6), s=>s.p.team, tuddyCard);
        return degWrap('ti-ball-american-football','Tuddy Targets',rows,'c-amber');
      }
      if(k==='elite'){
        const rows=_byGame(_fc(elite(),8,40), s=>s.p.team, s=>degRow(s.p.name,'#22c55e',{v1:M(s.val),l1:s.def.l,v2:'#'+s.dvpRank,l2:'softest'},
          posShort(s.p.position)+' · '+abv(s.p.team)+' v '+abv(s.opp)+' · '+M(s.val)+' '+s.def.l.toLowerCase()+multiLineTag(s.p.name,s.def.k)));
        return degWrap('ti-trophy','Elite Matchups',rows,'c-green');
      }
      if(k==='bunnies'){
        const rows=_byGame(_fc(bunnies(),6), b=>b.p.team, b=>{
          const head=b.lineBunny?(b.games+'/'+b.games+' cleared '+b.line)
            :(b.diffPct!=null?'+'+b.diffPct.toFixed(0)+'% v field':'best matchup');
          const sub=b.stat.l+' · vs '+abv(b.opp)+' '+M(b.thisAvg)+(b.best!=null?' (next best '+M(b.best)+')':'')+' · '+b.games+' H2H'+lineTag(b.p.name,b.stat.k);
          return degRow(b.p.name,'#3b82f6',{v1:M(b.thisAvg),l1:b.stat.l,v2:(b.lineBunny?b.games+'/'+b.games:(b.diffPct!=null?'+'+b.diffPct.toFixed(0)+'%':'—')),l2:(b.lineBunny?'cleared':'v field')},sub);
        });
        return degWrap('ti-carrot','Bunnies',rows,'c-blue');
      }
      if(k==='bogey'){
        const rows=_byGame(_fc(bogey(),6), b=>b.p.team, b=>{
          const head=b.lineBogey?('0/'+b.games+' cleared '+b.line)
            :(b.diffPct!=null?'-'+b.diffPct.toFixed(0)+'% v field':'worst matchup');
          const sub=b.stat.l+' · vs '+abv(b.opp)+' '+M(b.thisAvg)+(b.worst!=null?' (next worst '+M(b.worst)+')':'')+' · '+b.games+' H2H'+underTag(b.p.name,b.stat.k);
          return degRow(b.p.name,'#ef4444',{v1:M(b.thisAvg),l1:b.stat.l,v2:(b.lineBogey?'0/'+b.games:(b.diffPct!=null?'-'+b.diffPct.toFixed(0)+'%':'—')),l2:(b.lineBogey?'cleared':'v field')},sub);
        });
        return degWrap('ti-mood-sad','Bogey',rows,'c-red');
      }
      if(k==='streak'){
        const rows=_byGame(_fc(streakers(),6), s=>s.p.team, s=>degRow(s.p.name,'#f97316',{v1:s.streak,l1:'straight',v2:Math.round(s.rate*100)+'%',l2:'hit'},
          s.stat+' '+s.line+'+ · '+abbr(s.p.team)+(s.opp?' v '+abbr(s.opp):'')+' · hit '+Math.round(s.rate*100)+'%'+(s.dvpPct!=null?' · '+muTag(s.dvpPct):'')+streakOdds(s)));
        return degWrap('ti-flame','Streakers',rows,'c-amber');
      }
      if(k==='chunk'){
        const rows=_byGame(_fc(chunkPlays(),6,30), c=>c.p.team, chunkCard);
        return degWrap('ti-bolt','Chunk Plays',rows,'c-cyan');
      }
      if(k==='wrap'){
        const rows=_byGame(_fc(tackleMachines(),6,30), c=>c.p.team, tackleCard);
        return degWrap('ti-hammer','Tackle Machines',rows,'c-purple');
      }
      if(k==='ledger'){
        return ledgerPage();
      }
      if(k==='stack'){
        const rows=_fc(stackLab(),6,30).map(stackCard);
        return degWrap('ti-link','Stack Lab',rows,'c-cyan');
      }
      if(k==='usage'){
        const rows=_byGame(_fc(usageTrend(),8,30), c=>c.p.team, usageCard);
        return degWrap('ti-activity','Usage Trend',rows,'c-green');
      }
      if(k==='next'){
        const arr=_fc(nextManUp(),6,30);
        if(!arr.length) return _degenFocused?'':emptyState('ti-user-plus','Next Man Up is quiet','No meaningful starter on the slate is listed Out/Doubtful. Alerts fire from the injury report as it fills through the week.');
        return degWrap('ti-user-plus','Next Man Up',arr.map(nextCard),'c-amber');
      }
      if(k==='wx'){
        const arr=weatherWatch();
        if(!arr.length) return _degenFocused?'':emptyState('ti-wind','Weather Watch is calm','No outdoor game on the slate has wind \u2265'+WX_WIND+' km/h or rain in the forecast. Forecasts land inside the 16-day window before kickoff.');
        return degWrap('ti-wind','Weather Watch',arr.map(wxCard),'c-cyan');
      }
      if(k==='form'){
        const up=_byGame(_fc(formAlerts('spike'),6,25), a=>a.p.team, a=>degRow(a.p.name,'#22c55e',{v1:'▲ +'+Math.round(a.swing*100)+'%',l1:'swing',v2:M(a.l3),l2:'L3'},a.stat+' · L3 '+M(a.l3)+' vs season '+M(a.seasonAvg)+formNote('spike',a.runDvp,a.thisDvp)));
        const dn=_byGame(_fc(formAlerts('drop'),6,25), a=>a.p.team, a=>degRow(a.p.name,'#ef4444',{v1:'▼ '+Math.round(a.swing*100)+'%',l1:'swing',v2:M(a.l3),l2:'L3'},a.stat+' · L3 '+M(a.l3)+' vs season '+M(a.seasonAvg)+formNote('drop',a.runDvp,a.thisDvp)));
        return degWrap('ti-trending-up','Spiking',up,'c-green')+degWrap('ti-trending-down','Cooling',dn,'c-red');
      }
      if(k==='clamp'){
        const arr=clampWatch();
        if(arr===null) return _degenFocused?'':emptyState('ti-lock','Clamp Watch is arming','Corner coverage grades (PFR advanced defense) land with the next data build — the engine is wired and waiting.');
        const rows=_byGame(_fc(arr,8), t=>t.threat.team, t=>degRow(t.threat.name,'#a855f7',t.type==='shadow'?'SHADOW CB risk':'Top CB risk',
          t.threat.position+' · '+abbr(t.threat.team)+' — likely '+t.db.player+' ('+abbr(t.oppTeam)+')'+
          (t.db.grade?' · '+t.db.grade+' rating allowed':'')+(t.db.cmpPct?' · '+t.db.cmpPct.toFixed(0)+'% cmp':'')+
          (t.threat.tgtShare?' · '+t.threat.tgtShare.toFixed(0)+'% tgt share':''),t.threat.name));
        return degWrap('ti-lock','Clamp Watch',rows);
      }
      if(k==='push'){
        const rows=_fc(playoffPush(),6,20).map(pushCard);
        const wrap=degWrap('ti-ladder','Playoff Push',rows,'c-green');
        const st=_standings();
        return (st&&st.usedPrev&&rows.length?'<div class="tp-body-meta" style="border:0;margin:4px 0 0">Standings from the '+st.season+' season — updates live once '+CUR_SEASON()+' results land.</div>':'')+wrap;
      }
    // 'multi' intentionally omitted — the shell owns the Multi Builder.
      return emptyState('ti-flame','Coming next','This tile ports next.');
    }

    /* ============================================================================
     * CAPTURE HELPERS — normalise fired rows into settleable ledger records.
     * Mirrors AFL/signals.js captureOU/capture* so the Node pre-slate job can freeze
     * an NFL slate with the same contract.
     * ========================================================================== */
    function captureOU(teamList, ctx) {
      var recs = [];
      collectOU(teamList, 'over').forEach(function (r) {
        r.lineType = 'twoway'; r.market = r.market || r.statKey;
        var s = toSettleable('green_light', r, ctx); if (s) recs.push(s);
      });
      collectOU(teamList, 'under').forEach(function (r) {
        r.lineType = 'twoway'; r.market = r.market || r.statKey;
        var s = toSettleable('death_rider', r, ctx); if (s) recs.push(s);
      });
      return recs;
    }

    function captureStreakers(ctx) {
      var recs = [];
      streakers().forEach(function (s) {
        var rec = toSettleable('streakers', { p:s.p, opp:s.opp, team:s.p.team, market:s.statKey,
          line:s.line, side:'over', lineType:'milestone', score:s.streak,
          odds:(function () { var o = oddsFor(s.p.name, s.statKey); return o && o.over != null ? o.over : null; })(),
          book:(function () { var o = oddsFor(s.p.name, s.statKey); return o ? o.book : null; })() }, ctx);
        if (rec) recs.push(rec);
      });
      return recs;
    }

    function captureTuddy(ctx) {
      var recs = [];
      snags().forEach(function (s) {
        var o = oddsFor(s.p.name, 'anytimeTd');
        var rec = toSettleable('tuddy', { p:s.p, opp:s.opp, team:s.p.team, market:'anytimeTd',
          line:1, side:'over', lineType:'milestone', score:s.dvpRank,
          odds:(o && o.over != null ? o.over : null), book:(o ? o.book : null) }, ctx);
        if (rec) recs.push(rec);
      });
      return recs;
    }

    function captureChunk(ctx) {
      var recs = [];
      chunkPlays().forEach(function (c) {
        if (!c.posted) return;                       // only grade genuinely posted lines
        var o = oddsFor(c.p.name, c.def.k);
        var rec = toSettleable('chunk', { p:c.p, opp:c.opp, team:c.p.team, market:c.def.k,
          line:c.line, side:'over', lineType:'twoway', score:c.score,
          odds:(o && o.over != null ? o.over : null), book:(o ? o.book : null) }, ctx);
        if (rec) recs.push(rec);
      });
      return recs;
    }

    function captureWrap(ctx) {
      var recs = [];
      tackleMachines().forEach(function (t) {
        if (!t.posted) return;
        var o = oddsFor(t.p.name, 'tackles');
        var rec = toSettleable('wrap', { p:t.p, opp:t.opp, team:t.p.team, market:'tackles',
          line:t.line, side:'over', lineType:'twoway', score:t.score,
          odds:(o && o.over != null ? o.over : null), book:(o ? o.book : null) }, ctx);
        if (rec) recs.push(rec);
      });
      return recs;
    }

    function captureAll(teamList, ctx) {
      return [].concat(captureOU(teamList, ctx), captureStreakers(ctx), captureTuddy(ctx),
                       captureChunk(ctx), captureWrap(ctx));
    }

    /* ---- public surface ----------------------------------------------------
     * `tiles` is the map the shell calls when SPORT.key !== 'afl'. Each entry is a
     * pure COMPUTE function returning rows; the shell owns rendering, so one
     * markup/theme change doesn't have to be made twice.
     * --------------------------------------------------------------------- */
    var tiles = {
      paydirt: snags,          // Tuddy Targets
      elite:   elite,
      bunnies: bunnies,
      bogey:   bogey,
      streak:  streakers,
      chunk:   chunkPlays,
      wrap:    tackleMachines,
      usage:   usageTrend,
      next:    nextManUp,
      wx:      weatherWatch,
      stack:   stackLab,
      push:    playoffPush,
      clamp:   clampWatch,
      form:    formAlerts,
      ledger:  _ledgerCompute
    };

    return {
      // OU signals (multi-market — rows carry .market and .mktLabel)
      collectOU: collectOU,
      OU_MARKETS: OU_MARKETS,
      // research tiles
      tiles: tiles,
      snags: snags, elite: elite, bunnies: bunnies, bogey: bogey, streakers: streakers,
      chunkPlays: chunkPlays, tackleMachines: tackleMachines, usageTrend: usageTrend,
      nextManUp: nextManUp, weatherWatch: weatherWatch, stackLab: stackLab,
      playoffPush: playoffPush, clampWatch: clampWatch, formAlerts: formAlerts,
      ledger: _ledgerCompute, hotMatchups: hotMatchups, dvpRank: dvpRank,
      // NFL-specific helpers the shell may want (do NOT substitute the AFL versions)
      realisticLine2: realisticLine2, sameDivision: sameDivision,
      // render (the shell calls these when SPORT.key !== 'afl')
      renderTile: renderTile, snagsPage: snagsPage, elitePage: elitePage, ledgerPage: ledgerPage,
      // capture (Node pre-slate job)
      captureOU: captureOU, captureStreakers: captureStreakers, captureTuddy: captureTuddy,
      captureChunk: captureChunk, captureWrap: captureWrap, captureAll: captureAll
    };
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
