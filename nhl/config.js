/* JTT NHL sport module — config for the unified shell (AFL/index.html?sport=nhl).
   Hockey mapped onto the shell's sport-config contract; reuses the player-prop
   scoring engine (nhl/scoring.js) and hockey-adapted signals (nhl/signals.js).
   Data from nhl/data/* (built by build_nhl_data.py off the free NHL API). */
window.SPORT_CONFIG = {
  key: "nhl", dir: "nhl", name: "NHL",
  logoExt: ".svg", logoCdn: "https://assets.nhle.com/logos/nhl/svg/", logoCdnExt: "_light.svg",
  teamIds: {}, minutesFloor: 10,

  betStats: [["shots","Shots"],["points","Points"],["goals","Goals"],["assists","Assists"],["ppPoints","PP Points"]],
  mktNames: { shots:"Shots on Goal", points:"Points", goals:"Goals", assists:"Assists",
              ppPoints:"PP Points", saves:"Saves", pim:"PIM" },
  roundWord: "Game", lowCount: [],

  posOrder: ["C","L","R","D","G"],
  posColors: { C:"#38bdf8", L:"#22c55e", R:"#f59e0b", D:"#a78bfa", G:"#fb7185" },
  posShort: null, dvpStylePos: ["C","L","R","D"],

  display: [["minutes","TOI"],["goals","G"],["assists","A"],["points","Pts"],["shots","SOG"],
            ["ppPoints","PPP"],["ppGoals","PPG"],["pim","PIM"],["plusMinus","+/-"]],
  displaySets: { All: [["minutes","TOI"],["goals","G"],["assists","A"],["points","Pts"],["shots","SOG"],["ppPoints","PPP"]] },
  boxCols: [["minutes","TOI"],["goals","G"],["assists","A"],["points","PTS"],["shots","SOG"],["ppPoints","PPP"]],
  teamCols: [["goalsFor","GF"],["goalsAgainst","GA"],["shotsFor","SF"],["shotsAgainst","SA"]],

  cmpAliases: { sog:"shots", shot:"shots", shots:"shots", s:"shots",
                pt:"points", pts:"points", point:"points", points:"points", p:"points",
                g:"goals", goal:"goals", goals:"goals",
                a:"assists", ast:"assists", assist:"assists", assists:"assists", helpers:"assists",
                pp:"ppPoints", ppp:"ppPoints", pppoints:"ppPoints", sv:"saves", saves:"saves" },
  cmpLabels: { shots:"Shots", points:"Points", goals:"Goals", assists:"Assists", ppPoints:"PP Points", saves:"Saves" },
  compareStats: [["shots","Shots"],["points","Points"],["goals","Goals"],["assists","Assists"]],
  h2hKeys: [["shots","Shots"],["points","Points"],["goals","Goals"],["assists","Assists"]],

  oddsMkts: [["shots","Shots"],["points","Points"],["goals","Goals"],["assists","Assists"],["ppPoints","PP Points"]],
  multiMkts: [["shots","Shots"],["points","Points"],["goals","Goals"],["assists","Assists"]],
  nerdMkts: [["shots","Shots"],["points","Points"],["goals","Goals"],["assists","Assists"]],
  settingsMkts: [["shots","Shots"],["points","Points"],["goals","Goals"],["assists","Assists"],["ppPoints","PP Points"]],
  fxpMarkets: [["shots","SOG"],["points","Pts"],["goals","G"],["assists","A"]],
  cmbPick: [["shots","Shots",2.5],["points","Points",0.5],["goals","Goals",0.5],["assists","Assists",0.5],["ppPoints","PP Points",0.5]],
  dvMarkets: [["shots","Shots",2.5],["points","Points",0.5],["goals","Goals",0.5],["assists","Assists",0.5]],
  pcStats: [["shots","SOG"],["points","Pts"],["goals","G"],["assists","A"],["ppPoints","PPP"]],

  hideDvp: false,
  dvpStats: [["shots","Shots"],["points","Points"],["goals","Goals"],["assists","Assists"],["ppPoints","PP Points"]],
  dvpsOpts: [["shots","Shots"],["points","Points"],["goals","Goals"],["assists","Assists"]],
  jupiter: { markets: [["shots","Shots"],["points","Points"],["goals","Goals"],["assists","Assists"]], noPlayerOdds: false },

  liveWorker: "",
  viewDefaults: { sortKey:"points", statsStat:"points", teamsSort:"goalsFor" },
  dataFiles: {},

  crew: [
    { k:"multi",   n:"Multi Builder",  i:"ti-stack-2",        d:"Stacked legs",      odds:true },
    { k:"green",   n:"Green Lights",   i:"ti-traffic-lights", d:"Strong OVER",       odds:true },
    { k:"death",   n:"Death Riders",   i:"ti-skull",          d:"Strong UNDER",      odds:true },
    { k:"snipers", n:"Snipers",        i:"ti-target-arrow",   d:"Top SOG v soft D",  odds:false },
    { k:"producers",n:"Producers",     i:"ti-chart-line",     d:"Top pts v soft D",  odds:false },
    { k:"playmakers",n:"Playmakers",   i:"ti-hand-finger",    d:"Top ast v soft D",  odds:false },
    { k:"finishers",n:"Finishers",     i:"ti-ball-hockey",    d:"Top goals v soft D",odds:false },
    { k:"bunnies", n:"Bunnies",        i:"ti-carrot",         d:"Loves this opp",    odds:false },
    { k:"bogey",   n:"Bogey",          i:"ti-mood-sad",       d:"Struggles v opp",   odds:false },
    { k:"streak",  n:"Streakers",      i:"ti-flame",          d:"Hot form",          odds:false },
    { k:"form",    n:"Form Alerts",    i:"ti-trending-up",    d:"Trend shifts",      odds:false },
    { k:"pp",      n:"Power Play",     i:"ti-bolt",           d:"PP-point value",    odds:false },
    { k:"usage",   n:"Ice Time Trend", i:"ti-activity",       d:"TOI shifts",        odds:false }
  ],
  tileOrder: ["multi","green","death","snipers","producers","playmakers","finishers","bunnies","bogey","streak","form","pp","usage"]
};
