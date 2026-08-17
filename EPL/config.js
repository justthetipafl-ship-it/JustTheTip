/* JTT EPL — config.js  (window.SPORT_CONFIG)
   Soccer / Premier League config for the unified shell. Positions GK/DEF/MID/FWD.
   Player markets: shots, shots on target, goals, assists, key passes, tackles, fouls, saves, cards.

   Internal gamelog field contract (what the merge step must emit, and what signals read):
     min, goals, assists, xg, xa, tackles, saves, yellowCard, redCard, cards, cs, conceded,
     shots, shotsOn, keyPasses, foulsCommitted, foulsDrawn, passes
   Source -> contract mapping (built in the merge step):
     FPL:          G->goals A->assists xG->xg xA->xa tackles->tackles saves->saves
                   YC->yellowCard RC->redCard CS->cs GC->conceded min->min
     API-Football: shots->shots sot->shotsOn key_passes->keyPasses fouls->foulsCommitted
                   fouls_drawn->foulsDrawn passes->passes
   Shell SPORT-registry entry to add in index.html (holds the worker URLs):
     epl: { key:'epl', dir:'EPL', name:'EPL',
            oddsUrl:'https://jtt-odds.justthetipafl.workers.dev/odds.json?sport=EPL',
            liveWorker:'', resultsWorker:'https://jtt-afl-results.justthetipafl.workers.dev' }
*/
window.SPORT_CONFIG = {
  // prop-calculator stat chips — EPL's own markets (else it falls back to AFL disposals/marks)
  pcStats: [['shots','Shots'],['shotsOn','Shots on Target'],['goals','Goals'],['assists','Assists'],['tackles','Tackles'],['saves','Saves']],
  key: 'epl', dir: 'EPL', name: 'EPL', logoExt: '.png',

  // ---- markets / stat labels ----
  // bettable player-prop markets (Clash / Last Meeting / Matchup / Stat Leaders)
  betStats: [['shots','Shots'],['shotsOn','Shots on Target'],['goals','Goals'],['assists','Assists'],
             ['tackles','Tackles'],['foulsCommitted','Fouls'],['keyPasses','Key Passes'],['saves','Saves'],['cards','Cards']],
  // Multi Builder markets (else it falls back to AFL disposals/marks/kicks - only 'tackles' overlaps EPL)
  multiMkts: [['shots','Shots'],['shotsOn','Shots on Target'],['goals','Goals'],['assists','Assists'],
              ['tackles','Tackles'],['keyPasses','Key Passes'],['foulsCommitted','Fouls'],['saves','Saves'],['cards','Cards']],
  mktNames: { shots:'Shots', shotsOn:'Shots on Target', goals:'Goals', assists:'Assists', tackles:'Tackles',
              foulsCommitted:'Fouls', foulsDrawn:'Fouls Drawn', keyPasses:'Key Passes', passes:'Passes',
              saves:'Saves', conceded:'Conceded', cards:'Cards', cs:'Clean Sheet', xg:'xG', xa:'xA' },
  roundWord: 'Gameweek',
  lowCount: ['xg','xa'],   // shown to 2dp
  dec2: ['xg','xa'],

  // ---- positions ----
  posOrder: ['GK','DEF','MID','FWD'],
  posColors: { GK:'#eab308', DEF:'#3b82f6', MID:'#22c55e', FWD:'#ef4444' },
  posShort: { GK:'GK', DEF:'DEF', MID:'MID', FWD:'FWD' },

  // ---- display columns (Stats table + player modal), position-aware ----
  display: [['shots','Sh'],['shotsOn','SoT'],['goals','G'],['assists','A'],['keyPasses','KP'],
            ['tackles','Tkl'],['foulsCommitted','Fls'],['xg','xG'],['xa','xA']],
  displaySets: {
    All: [['shots','Sh'],['shotsOn','SoT'],['goals','G'],['assists','A'],['keyPasses','KP'],
          ['tackles','Tkl'],['foulsCommitted','Fls'],['xg','xG'],['xa','xA']],
    GK:  [['saves','Sv'],['conceded','GC'],['cs','CS'],['passes','Pass'],['xg','xG']],
    DEF: [['tackles','Tkl'],['shots','Sh'],['shotsOn','SoT'],['goals','G'],['assists','A'],
          ['foulsCommitted','Fls'],['keyPasses','KP'],['xg','xG'],['xa','xA']],
  },

  // ---- markets across features ----
  oddsMkts: [['shots','Shots'],['shotsOn','Shots on Target'],['goals','Goals'],['assists','Assists'],
             ['tackles','Tackles'],['foulsCommitted','Fouls'],['saves','Saves'],['cards','Cards']],
  multiMkts: [['shots','Shots'],['shotsOn','Shots on Target'],['goals','Goals'],['assists','Assists'],['tackles','Tackles'],['saves','Saves']],
  nerdMkts: [['shots','Shots'],['shotsOn','Shots on Target'],['goals','Goals'],['assists','Assists'],['tackles','Tackles']],
  settingsMkts: [['shots','Shots'],['shotsOn','Shots on Target'],['goals','Goals'],['assists','Assists'],
                 ['tackles','Tackles'],['foulsCommitted','Fouls'],['saves','Saves'],['cards','Cards']],
  compareStats: [['shots','Shots'],['shotsOn','SoT'],['goals','Goals'],['assists','Assists'],
                 ['tackles','Tackles'],['keyPasses','Key Passes'],['xg','xG'],['xa','xA']],
  boxCols: [['shots','Sh'],['shotsOn','SoT'],['goals','G'],['assists','A'],['tackles','Tkl'],['foulsCommitted','Fls'],['saves','Sv']],
  cmpLabels: { shots:'Shots', shotsOn:'Shots on Target', goals:'Goals', assists:'Assists', tackles:'Tackles',
               foulsCommitted:'Fouls', keyPasses:'Key Passes', saves:'Saves', cards:'Cards' },
  cmpAliases: { shots:'shots', shot:'shots', sot:'shotsOn', 'shots on target':'shotsOn', sog:'shotsOn',
                goals:'goals', goal:'goals', scorer:'goals', assists:'assists', assist:'assists',
                tackles:'tackles', tackle:'tackles', tkl:'tackles', fouls:'foulsCommitted', foul:'foulsCommitted',
                'key passes':'keyPasses', keypasses:'keyPasses', kp:'keyPasses', saves:'saves', save:'saves',
                cards:'cards', card:'cards', booking:'cards' },
  teamCols: [['goals','G'],['shots','Sh'],['shotsOn','SoT'],['corners','Cor'],['tackles','Tkl'],['foulsCommitted','Fls'],['cards','Crd']],

  // Déjà Vu / Check My Bet default lines
  dvMarkets: [['shots','Shots',1.5],['shotsOn','Shots on Target',0.5],['goals','Goals',0.5],['assists','Assists',0.5],
              ['tackles','Tackles',1.5],['foulsCommitted','Fouls',0.5],['saves','Saves',2.5]],
  cmbPick: [['shots','Shots',1.5],['shotsOn','Shots on Target',0.5],['goals','Goals',0.5],['assists','Assists',0.5],
            ['tackles','Tackles',1.5],['saves','Saves',2.5]],
  fxpMarkets: [['shots','Shots'],['shotsOn','SoT'],['goals','Goals'],['assists','Assists'],['tackles','Tackles'],['saves','Saves']],
  h2hKeys: [['shots','Shots'],['shotsOn','SoT'],['goals','Goals'],['assists','Assists'],['tackles','Tackles'],['keyPasses','Key Passes']],

  // ---- DVP (opponent difficulty by stat conceded to position) ----
  dvpStats: [['shots','Shots'],['shotsOn','Shots on Target'],['goals','Goals'],['assists','Assists'],['tackles','Tackles']],
  dvpsOpts: [['shots','Shots'],['shotsOn','Shots on Target'],['goals','Goals'],['tackles','Tackles']],
  dvpStylePos: { GK:'GK', DEF:'DEF', MID:'MID', FWD:'FWD',
                 CB:'DEF', LB:'DEF', RB:'DEF', FB:'DEF', WB:'DEF',
                 DM:'MID', CM:'MID', AM:'MID', LM:'MID', RM:'MID', W:'MID', LW:'FWD', RW:'FWD',
                 ST:'FWD', CF:'FWD', SS:'FWD' },

  // ---- O/U line ladders per market (pick the highest line the avg clears) ----
  ladders: { shots:[0.5,1.5,2.5,3.5,4.5], shotsOn:[0.5,1.5,2.5,3.5], goals:[0.5,1.5,2.5], assists:[0.5,1.5],
             tackles:[0.5,1.5,2.5,3.5,4.5], foulsCommitted:[0.5,1.5,2.5,3.5], keyPasses:[0.5,1.5,2.5,3.5],
             saves:[0.5,1.5,2.5,3.5,4.5,5.5], passes:[19.5,29.5,39.5,49.5,59.5,69.5] },

  // ---- season-form filter (replaces the WC tournament-form date) ----
  seasonStart: '2026-08-21',

  viewDefaults: { sortKey: 'shots', statsStat: 'shots', teamsSort: 'goals' },

  // extra per-sport data files (merged with the shell's common set)
  noPlayerOdds: true,   // RapidOdds soccer = team markets only; Jupiter (player props) can't run
  dataFiles: { fpl_players: 'data/fpl_players.json', fpl_gamelogs: 'data/fpl_gamelogs.json',
               apifootball: 'data/apifootball_stats.json', referees: 'data/referees.json' },

  // ---- Degen Crew tiles (ported from the WC tool's CREW_TABS) ----
  crew: [
    { k:'board',          n:"Today's Board", i:'ti-clipboard-list',        d:'Best plays ranked' },
    { k:'multi',          n:'Multi Builder', i:'ti-stack-2',               d:'Stacked legs' },
    { k:'locked-in',      n:'Locked In',     i:'ti-lock-square-rounded',   d:'Hitting their line in 4 of last 5' },
    { k:'falling-off',    n:'Falling Off',   i:'ti-trending-down',         d:'Trending under — avoid the trap' },
    { k:'on-a-run',       n:'On a Run',      i:'ti-run',                   d:'Active streaks worth riding' },
    { k:'first-goal',     n:'First Goal',    i:'ti-target-arrow',          d:'Most likely opener candidates' },
    { k:'tap-ins',        n:'Tap-Ins',       i:'ti-hand-finger',           d:'High shots-to-goals conversion' },
    { k:'penalty-kings',  n:'Penalty Kings', i:'ti-crown',                 d:'Confirmed takers and their %' },
    { k:'spam-square',    n:'Spam Square',   i:'ti-square-letter-x',       d:'Card magnets — book it' },
    { k:'goals-galore',   n:'Goals Galore',  i:'ti-arrows-up-down',        d:'Overs and unders, side by side' },
    { k:'corner-storm',   n:'Corner Storm',  i:'ti-flag-3',                d:'Match corner totals' },
    { k:'mismatch',       n:'Mismatch Alert',i:'ti-bolt',                  d:'Attacker vs leaky defence' },
    { k:'brick-wall',     n:'Brick Wall',    i:'ti-shield',                d:'Goalkeepers in line for saves' },
    { k:'tackle-machines',n:'Tackle Machines',i:'ti-shield-half',          d:'Defensive volume — tackles O/U' },
    { k:'fouled-again',   n:'Fouled Again',  i:'ti-hand-stop',             d:'Foul magnets — fouls O/U' },
    { k:'golden-boot',    n:'Golden Boot',   i:'ti-shoe',                  d:'Season scoring race' },
    { k:'form-alerts',    n:'Form Alerts',   i:'ti-temperature-celsius',   d:'Hot streaks and cold spells' },
  ],
  tileOrder: ['locked-in','falling-off','on-a-run','first-goal','tap-ins','penalty-kings','spam-square',
              'goals-galore','corner-storm','mismatch','brick-wall','tackle-machines','fouled-again','golden-boot','form-alerts'],
};
