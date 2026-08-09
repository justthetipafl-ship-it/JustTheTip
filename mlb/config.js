/* JTT MLB — config.js  (window.SPORT_CONFIG)
   Sport vocabulary for the unified shell. Batters + pitchers share one player pool
   (role: 'bat'|'pitch'); markets and display sets are role/position aware. */
window.SPORT_CONFIG = {
  // prop-calculator stat chips — MLB's own markets (else it falls back to AFL disposals/marks)
  pcStats: [['H','Hits'],['TB','Total Bases'],['HR','Home Runs'],['RBI','RBIs'],['R','Runs'],['K','Strikeouts']],
  key: 'mlb', dir: 'mlb', name: 'MLB', logoExt: '.svg',

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
  oddsMkts: [['H','Hits'],['TB','Total Bases'],['HR','Home Runs'],['RBI','RBIs'],['R','Runs'],['SB','Stolen Bases'],['K','Strikeouts']],
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

  // ---- view defaults ----
  viewDefaults: { sortKey: 'H', statsStat: 'H', teamsSort: 'R' },

  // ---- per-sport extra data files (beyond the common set) ----
  dataFiles: {},

  // ---- Degen Crew catalog (game-centric — MLB signals live in mlb/signals.js) ----
  crew: [
    { k:'board',   n:"Today's Board", i:'ti-clipboard-list', d:'Best plays ranked' },
    { k:'multi',   n:'Multi Builder', i:'ti-stack-2',        d:'Stacked legs' },
    { k:'hot',     n:'Hot Bats',      i:'ti-flame',          d:'Hitters trending up' },
    { k:'cold',    n:'Cold Bats',     i:'ti-snowflake',      d:'Hitless skids — fade or avoid' },
    { k:'whiff',   n:'Whiff Risk',    i:'ti-circle-x',       d:'Bats likely to strike out vs high-K arms' },
    { k:'ktargets',n:'K Targets',     i:'ti-target-arrow',   d:'Pitchers into whiff-prone lineups' },
    { k:'longball',n:'Long Ball',     i:'ti-ball-baseball',  d:'HR spots — power into hitter parks' },
    { k:'platoon', n:'Platoon Edge',  i:'ti-arrows-shuffle', d:'Big handedness-split mismatches' },
    { k:'coldarm', n:'Hittable Arms', i:'ti-temperature',    d:'Bats vs contact-prone starters' },
    { k:'runners', n:'Table Setters', i:'ti-arrow-up-right', d:'Top-of-order run scorers vs wild arms' },
    { k:'bunny',   n:'Bunnies',       i:'ti-mood-happy',     d:'Batters who own today\'s starter' },
    { k:'bogey',   n:'Bogeys',        i:'ti-mood-sad',       d:'Batters owned by today\'s starter' },
    { k:'wheels',  n:'Wheels',        i:'ti-run',            d:'Steal spots vs slow-to-plate arms' },
    { k:'due',     n:'Due / Unlucky', i:'ti-trending-up',    d:'Underperforming their Statcast — back' },
    { k:'hothand', n:'Running Hot',   i:'ti-trending-down',  d:'Overperforming their Statcast — fade' }
  ],
  tileOrder: ['hot','cold','whiff','ktargets','longball','platoon','coldarm','runners','bunny','bogey','wheels','due','hothand']
};
