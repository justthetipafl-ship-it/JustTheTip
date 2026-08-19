/* JTT MLB — config.js  (window.SPORT_CONFIG)
   Sport vocabulary for the unified shell. Batters + pitchers share one player pool
   (role: 'bat'|'pitch'); markets and display sets are role/position aware. */
window.SPORT_CONFIG = {
  key:'mlb', teamDecimals:2, leadersPerGame:true, dir: 'mlb', name: 'MLB', logoExt: '.svg',

  // ---- markets / stat labels ----
  // bettable player-prop markets (Clash / Last Meeting / Matchup / Stat Leaders)
  betStats: [['H','Hits'],['TB','Total Bases'],['HR','Home Runs'],['RBI','RBIs'],['R','Runs'],['SB','Stolen Bases'],['BB','Walks']],
  mktNames: { H:'Hits', TB:'Total Bases', HR:'Home Runs', RBI:'RBIs', R:'Runs', SB:'Stolen Bases', BB:'Walks', K:'Strikeouts', IP:'Innings', ERA:'ERA' },
  roundWord: 'Game',
  lowCount: ['AVG','OBP','SLG','OPS','ERA','WHIP','K9','BB9','HR9','oppAVG'],  // shown to 2dp

  // ---- positions ----
  posOrder: ['SP','RP','C','1B','2B','3B','SS','LF','CF','RF','DH'],
  posColors: { SP:'#ef4444', RP:'#f97316', C:'#a855f7', '1B':'#3b82f6', '2B':'#22c55e', '3B':'#eab308', SS:'#06b6d4', LF:'#8b5cf6', CF:'#14b8a6', RF:'#ec4899', DH:'#64748b' },
  posShort: null,  // MLB positions already short — passthrough

  // ---- display columns (scanner table + player modal), per-position ----
  display: [['H','H'],['TB','TB'],['HR','HR'],['RBI','RBI'],['R','R'],['AVG','AVG'],['OBP','OBP'],['SLG','SLG'],['SB','SB'],['BB','BB'],['SO','K']],
  posMarkets: {   // pitchers bet strikeouts only; batters never show pitching props
    SP:['K'], RP:['K'],
    C:['H','TB','HR','RBI','R','SB','BB'], '1B':['H','TB','HR','RBI','R','SB','BB'], '2B':['H','TB','HR','RBI','R','SB','BB'], '3B':['H','TB','HR','RBI','R','SB','BB'], SS:['H','TB','HR','RBI','R','SB','BB'],
    LF:['H','TB','HR','RBI','R','SB','BB'], CF:['H','TB','HR','RBI','R','SB','BB'], RF:['H','TB','HR','RBI','R','SB','BB'], DH:['H','TB','HR','RBI','R','SB','BB'], TWP:['H','TB','HR','RBI','R','SB','K']
  },
  displaySets: {
    All: [['H','H'],['TB','TB'],['HR','HR'],['RBI','RBI'],['R','R'],['AVG','AVG'],['OBP','OBP'],['SLG','SLG'],['SB','SB'],['BB','BB'],['SO','K']],
    SP:  [['K','K'],['IP','IP'],['ERA','ERA'],['WHIP','WHIP'],['K9','K/9'],['BB9','BB/9'],['HR9','HR/9'],['oppAVG','oAVG'],['H','H'],['BB','BB']],
    RP:  [['K','K'],['IP','IP'],['ERA','ERA'],['WHIP','WHIP'],['K9','K/9'],['BB9','BB/9'],['HR9','HR/9'],['oppAVG','oAVG'],['H','H'],['BB','BB']]
  },

  // ---- Check My Bet parser ----
  cmpAliases: {
    'strikeouts':'K','strikeout':'K','punchouts':'K','punch outs':'K','ks':'K','k':'K',
    'total bases':'TB','total base':'TB','tb':'TB','bases':'TB',
    'home runs':'HR','home run':'HR','hr':'HR','homer':'HR','homers':'HR','dinger':'HR','bomb':'HR',
    'rbis':'RBI','rbi':'RBI','runs batted in':'RBI','runs batted':'RBI',
    'stolen bases':'SB','stolen base':'SB','steals':'SB','sb':'SB','steal':'SB',
    'walks':'BB','walk':'BB','bb':'BB','base on balls':'BB','free pass':'BB',
    'runs':'R','run':'R',
    'hits':'H','hit':'H'
  },
  cmpLabels: { H:'Hits', TB:'Total Bases', HR:'Home Runs', RBI:'RBIs', R:'Runs', SB:'Stolen Bases', BB:'Walks', K:'Strikeouts' },

  // ---- head-to-head season averages (focused fixture bar chart) uses team stats ----
  h2hKeys: [['R','Runs'],['H','Hits'],['HR','Home Runs'],['TB','Total Bases'],['BB','Walks'],['SO','Strikeouts']],

  // ---- team stats table columns (from team forStats) ----
  teamCols: [['R','R'],['H','H'],['HR','HR'],['TB','TB'],['BB','BB'],['SO','SO'],['SB','SB']],

  // ---- markets across features ----
  oddsMkts: [['H','Hits'],['TB','Total Bases'],['HR','Home Runs'],['RBI','RBIs'],['R','Runs'],['K','Strikeouts']],   // SB has no odds market in RapidOddsAPI
  multiMkts: [['H','Hits'],['TB','Total Bases'],['HR','Home Runs'],['RBI','RBIs'],['R','Runs'],['K','Strikeouts']],
  nerdMkts: [['H','Hits'],['TB','Total Bases'],['HR','Home Runs'],['RBI','RBIs'],['K','Strikeouts']],
  settingsMkts: [['H','Hits'],['TB','Total Bases'],['HR','Home Runs'],['RBI','RBIs'],['R','Runs'],['SB','Stolen Bases'],['K','Strikeouts']],
  compareStats: [['H','Hits'],['TB','Total Bases'],['HR','Home Runs'],['RBI','RBIs'],['R','Runs'],['AVG','AVG'],['OPS','OPS']],
  boxCols: [['H','H'],['TB','TB'],['HR','HR'],['RBI','RBI'],['R','R'],['BB','BB'],['SO','K']],
  cmbPick: [['H','Hits',1.5],['TB','Total Bases',1.5],['HR','Home Runs',0.5],['RBI','RBIs',0.5],['R','Runs',0.5],['SB','Stolen Bases',0.5],['K','Strikeouts',5.5]],
  fxpMarkets: [['H','Hits'],['TB','Total Bases'],['HR','HR'],['RBI','RBI'],['R','Runs'],['K','Ks']],
  dvMarkets: [['H','Hits',1.5],['TB','Total Bases',1.5],['HR','Home Runs',0.5],['RBI','RBIs',0.5],['R','Runs',0.5],['K','Strikeouts',5.5]],

  // ---- DVP (team allowance) options ----
  dvpStats: [['H','Hits'],['HR','Home Runs'],['R','Runs'],['BB','Walks'],['SO','Strikeouts']],
  dvpsOpts: [['H','Hits'],['HR','Home Runs'],['R','Runs'],['SO','Strikeouts']],

  // ---- Jupiter scatter (batter markets) ----
  jupiter: {
    markets: [['H','Hits'],['TB','Total Bases'],['HR','HR'],['RBI','RBI'],['R','Runs']],
    mlab: { H:'hits', TB:'total bases', HR:'home runs', RBI:'RBIs', R:'runs' },
    base: { H:1, TB:1.5, HR:0.5, RBI:0.5, R:0.5 }
  },

  // ---- prop calculator markets ----
  pcStats: [['H','Hits'],['TB','TB'],['HR','HR'],['RBI','RBI'],['R','R'],['SB','SB'],['K','K']],

  // ---- logos via MLB Stats CDN by team id ----
  liveWorker: 'https://jtt-mlb-live.justthetipafl.workers.dev',   // MLB Stats API proxy Worker (deploy jtt-mlb-live-worker.js)
  hideDvp: true,
  logoCdn: 'https://www.mlbstatic.com/team-logos/',
  teamIds: { ATH:133, ATL:144, AZ:109, BAL:110, BOS:111, CHC:112, CIN:113, CLE:114, COL:115, CWS:145,
    DET:116, HOU:117, KC:118, LAA:108, LAD:119, MIA:146, MIL:158, MIN:142, NYM:121, NYY:147,
    PHI:143, PIT:134, SD:135, SEA:136, SF:137, STL:138, TB:139, TEX:140, TOR:141, WSH:120 },

  // ---- view defaults ----
  viewDefaults: { sortKey: 'H', statsStat: 'H', teamsSort: 'R' },

  // ---- per-sport extra data files (beyond the common set) ----
  dataFiles: {},

  // ---- Degen Crew catalog — matches the standalone /mlb/ tool's 14 signals ----
  crew: [
    { k:'board',       n:"Today's Board", i:'ti-clipboard-list',   d:'Best plays ranked' },
    { k:'multi',       n:'Multi Builder',  i:'ti-stack-2',          d:'Stacked legs' },
    { k:'greenlights', n:'Green Lights',   i:'ti-circle-check',     d:'Best batter matchups on the slate' },
    { k:'platoon',     n:'Sluggers',       i:'ti-arrows-left-right',d:'Big handedness-split power edges' },
    { k:'runners',     n:'Runners',        i:'ti-arrow-up-right',   d:'Top-of-order run scorers vs wild arms' },
    { k:'rbimen',      n:'RBI Men',        i:'ti-target',           d:'Run-producing bats in RBI spots' },
    { k:'freepasses',  n:'Free Passes',    i:'ti-walk',             d:'Patient hitters vs wild arms' },
    { k:'grinders',    n:'Grinders',       i:'ti-flame',            d:'Starters in tough spots — fade' },
    { k:'inningseaters',n:'Innings Eaters',i:'ti-clock-hour-9',     d:'Efficient starters who go deep' },
    { k:'due',         n:'Due / Unlucky',  i:'ti-trending-up',      d:'Underperforming their Statcast — back' },
    { k:'ktargets',    n:'Strike Time',    i:'ti-ball-baseball',    d:'Pitchers into whiff-prone lineups' },
    { k:'longball',    n:'Homers',         i:'ti-bolt',             d:'HR spots — power into hitter parks' },
    { k:'wheels',      n:'Sneaky Buggers', i:'ti-run',              d:'Steal spots vs slow-to-plate arms' },
    { k:'streakers',   n:'Streakers',      i:'ti-flame',            d:'Live hitting streaks (5+ games)' },
    { k:'bunny',       n:'Bunnies',        i:'ti-mood-happy',       d:"Batters who own today's starter" },
    { k:'whiff',       n:'Whiff Risk',     i:'ti-circle-x',         d:'Bats likely to K vs high-K arms' },
    { k:'hothand',     n:'Running Hot',    i:'ti-trending-down',    d:'Overperforming their Statcast — fade' },
    { k:'cold',        n:'Cold Bats',      i:'ti-snowflake',        d:'Hitless skids — fade or avoid' },
    { k:'deathriders', n:'Death Riders',   i:'ti-skull',            d:'Worst batter matchups — fade' },
    { k:'bogey',       n:'Bogey',          i:'ti-mood-sad',         d:"Batters owned by today's starter" }
  ],
  tileOrder: ['greenlights','platoon','runners','rbimen','freepasses','longball','wheels','streakers','bunny','due','ktargets','grinders','inningseaters','whiff','hothand','cold','deathriders','bogey']
};
