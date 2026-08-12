/* JTT NBL sport module — config for the unified shell (AFL/index.html?sport=nbl).
   Basketball mapped onto the shell's sport-config contract, modelled on mlb/config.js.
   Data comes from nbl/data/* (built by build_nbl.py from nblR). */
window.SPORT_CONFIG = {
  key: "nbl",
  dir: "nbl",
  name: "NBL",
  logoExt: ".png",
  minutesFloor: 12,
  logoCdn: "",                 // logos are per-team absolute URLs carried on teams.json (t.logo)
  logoCdnExt: "",
  teamIds: {},                 // not needed — logo URLs live on the team rows

  // ---- bettable player-prop markets ----
  betStats: [["points","Points"],["rebounds","Rebounds"],["assists","Assists"],
             ["threes","Threes"],["pra","PRA"],["steals","Steals"],["blocks","Blocks"]],
  mktNames: { points:"Points", rebounds:"Rebounds", assists:"Assists", threes:"Threes",
              pra:"PRA", pr:"P+R", pa:"P+A", ra:"R+A", steals:"Steals", blocks:"Blocks",
              stocks:"Stl+Blk", turnovers:"Turnovers" },
  roundWord: "Game",
  lowCount: [],                // no rate-only stats surfaced as markets

  // ---- positions ----
  posOrder: ["G","F","C"],
  posColors: { G:"#3b82f6", F:"#f59e0b", C:"#ec4899" },
  posShort: null,
  dvpStylePos: ["G","F","C"],

  // ---- stats table / box score columns ----
  display: [["minutes","Min"],["points","Pts"],["rebounds","Reb"],["oreb","OR"],["dreb","DR"],
            ["assists","Ast"],["threes","3PM"],["threesAtt","3PA"],["fgm","FGM"],["fga","FGA"],
            ["ftm","FTM"],["fta","FTA"],["steals","Stl"],["blocks","Blk"],["turnovers","TO"],
            ["fouls","PF"],["pra","PRA"]],
  displaySets: { All: [["minutes","Min"],["points","Pts"],["rebounds","Reb"],["assists","Ast"],
                       ["threes","3PM"],["steals","Stl"],["blocks","Blk"],["turnovers","TO"],["pra","PRA"]] },
  boxCols: [["minutes","MIN"],["points","PTS"],["rebounds","REB"],["assists","AST"],
            ["threes","3PM"],["steals","STL"],["blocks","BLK"],["turnovers","TO"]],
  teamCols: [["points","Pts"],["rebounds","Reb"],["assists","Ast"],["threes","3PM"],
             ["threesAtt","3PA"],["fgm","FGM"],["fga","FGA"],["ftm","FTM"],["fta","FTA"],
             ["oreb","OR"],["steals","Stl"],["blocks","Blk"],["turnovers","TO"]],

  // ---- compare / CMB aliases ----
  cmpAliases: { pts:"points", point:"points", points:"points", p:"points",
                reb:"rebounds", rebound:"rebounds", rebounds:"rebounds", boards:"rebounds", r:"rebounds",
                ast:"assists", assist:"assists", assists:"assists", dimes:"assists", a:"assists",
                three:"threes", threes:"threes", "3pm":"threes", "3s":"threes", triples:"threes",
                pra:"pra", "p+r+a":"pra", stl:"steals", steal:"steals", steals:"steals",
                blk:"blocks", block:"blocks", blocks:"blocks", "stl+blk":"stocks", stocks:"stocks",
                to:"turnovers", turnover:"turnovers", turnovers:"turnovers", tov:"turnovers" },
  cmpLabels: { points:"Points", rebounds:"Rebounds", assists:"Assists", threes:"Threes",
               pra:"PRA", pr:"P+R", pa:"P+A", ra:"R+A", steals:"Steals", blocks:"Blocks",
               stocks:"Stl+Blk", turnovers:"Turnovers" },
  compareStats: [["points","Points"],["rebounds","Rebounds"],["assists","Assists"],
                 ["threes","Threes"],["pra","PRA"]],
  h2hKeys: [["points","Points"],["rebounds","Rebounds"],["assists","Assists"],["threes","Threes"]],

  // ---- market menus (Scanner / CMB / Multi / Nerd / Settings / FXP / DV) ----
  oddsMkts: [["points","Points"],["rebounds","Rebounds"],["assists","Assists"],["threes","Threes"],
             ["pra","PRA"],["steals","Steals"],["blocks","Blocks"]],
  multiMkts: [["points","Points"],["rebounds","Rebounds"],["assists","Assists"],["threes","Threes"],["pra","PRA"]],
  nerdMkts: [["points","Points"],["rebounds","Rebounds"],["assists","Assists"],["threes","Threes"],["pra","PRA"]],
  settingsMkts: [["points","Points"],["rebounds","Rebounds"],["assists","Assists"],["threes","Threes"],
                 ["pra","PRA"],["steals","Steals"],["blocks","Blocks"]],
  fxpMarkets: [["points","Points"],["rebounds","Rebounds"],["assists","Assists"],["threes","3PM"],["pra","PRA"]],
  cmbPick: [["points","Points",15.5],["rebounds","Rebounds",5.5],["assists","Assists",3.5],
            ["threes","Threes",1.5],["pra","PRA",24.5],["steals","Steals",0.5],["blocks","Blocks",0.5]],
  dvMarkets: [["points","Points",15.5],["rebounds","Rebounds",5.5],["assists","Assists",3.5],
              ["threes","Threes",1.5],["pra","PRA",24.5]],
  pcStats: [["points","Pts"],["rebounds","Reb"],["assists","Ast"],["threes","3PM"],
            ["pra","PRA"],["steals","Stl"],["blocks","Blk"]],

  // ---- DVP ----
  hideDvp: false,
  dvpStats: [["points","Points"],["rebounds","Rebounds"],["assists","Assists"],["threes","Threes"],
             ["steals","Steals"],["blocks","Blocks"],["pra","PRA"],["pr","P+R"],["pa","P+A"],
             ["ra","R+A"],["stocks","Stl+Blk"],["turnovers","Turnovers"]],
  dvpsOpts: [["points","Points"],["rebounds","Rebounds"],["assists","Assists"],["threes","Threes"]],

  // ---- Jupiter (radar) ----
  jupiter: { markets: [["points","Points"],["rebounds","Rebounds"],["assists","Assists"],
                       ["threes","Threes"],["pra","PRA"]],
             noPlayerOdds: false },

  liveWorker: "",
  viewDefaults: { sortKey:"points", statsStat:"points", teamsSort:"points" },
  dataFiles: {},               // shell defaults + meta.gamelogFiles drive loading

  // ---- Degen Crew (basketball engines; ported into nbl/signals.js) ----
  crew: [
    { k:"multi",   n:"Multi Builder",  i:"ti-stack-2",        d:"Stacked legs",        odds:true },
    { k:"green",   n:"Green Lights",   i:"ti-traffic-lights", d:"Strong OVER",         odds:true },
    { k:"death",   n:"Death Riders",   i:"ti-skull",          d:"Strong UNDER",        odds:true },
    { k:"slash",   n:"Slashers",       i:"ti-sword",          d:"Top pts v soft D",    odds:false },
    { k:"splash",  n:"Splashers",      i:"ti-droplet",        d:"Top 3PM v soft D",    odds:false },
    { k:"dish",    n:"Dishers",        i:"ti-hand-finger",    d:"Top ast v soft D",    odds:false },
    { k:"board",   n:"Boarders",       i:"ti-wall",           d:"Top reb v soft D",    odds:false },
    { k:"bunnies", n:"Bunnies",        i:"ti-carrot",         d:"Loves this opp",      odds:false },
    { k:"bogey",   n:"Bogey",          i:"ti-mood-sad",       d:"Struggles v opp",     odds:false },
    { k:"streak",  n:"Streakers",      i:"ti-flame",          d:"Hot form",            odds:false },
    { k:"form",    n:"Form Alerts",    i:"ti-trending-up",    d:"Trend shifts",        odds:false },
    { k:"dd",      n:"Double Trouble", i:"ti-stack-3",        d:"Double-double watch", odds:false },
    { k:"usage",   n:"Usage Trend",    i:"ti-activity",       d:"Role shifts",         odds:false },
  ],
  tileOrder: ["multi","green","death","slash","splash","dish","board","bunnies","bogey","streak","form","dd","usage"]
};
