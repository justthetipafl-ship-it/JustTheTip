/* JTT sport config — NFL. Loaded by the unified shell as window.SPORT_CONFIG. */
window.SPORT_CONFIG = {
  key: 'nfl',
  name: 'NFL',
  jupiter: {
    markets: [['passYds','Pass Yd'],['rushYds','Rush Yd'],['recYds','Rec Yd'],['receptions','Rec'],['rushAtt','Rush Att'],['passTds','Pass TD'],['tackles','Tackles']],
    mlab:    { passYds:'pass yd', rushYds:'rush yd', recYds:'rec yd', receptions:'rec', rushAtt:'rush att', passTds:'pass TD', tackles:'tackles' },
    base:    { passYds:225, rushYds:45, recYds:45, receptions:4, rushAtt:12, passTds:1.5, tackles:6 },
  },
  mktNames: { passYds:'Pass Yds', passAtt:'Pass Att', passTds:'Pass TDs', rushYds:'Rush Yds', rushAtt:'Rush Att', receptions:'Receptions', recYds:'Rec Yds', rushRecYds:'Rush+Rec Yds', anytimeTd:'Anytime TD', tackles:'Tackles+Ast' },
  pcStats:  [['passYds','PaYds'],['passAtt','PaAtt'],['passTds','PaTD'],['rushYds','RuYds'],['rushAtt','Car'],['receptions','Rec'],['recYds','ReYds'],['rushRecYds','R+R'],['anytimeTd','ATD'],['fanPts','FanPts'],['longRec','LngRec'],['longRush','LngRsh'],['longComp','LngCmp'],['tackles','Tk+A'],['soloTk','Solo']],
  cmbPick:  [['passYds','Pass Yds',180],['rushYds','Rush Yds',40],['recYds','Rec Yds',40],['receptions','Receptions',3],['rushAtt','Rush Att',10],['passTds','Pass TDs',1.2],['anytimeTd','Anytime TD',0.35],['tackles','Tackles+Ast',6]],
  fxpMarkets: [['passYds','Pass Yd'],['rushYds','Rush Yd'],['recYds','Rec Yd'],['receptions','Rec'],['rushAtt','Rush Att'],['passTds','Pass TD'],['tackles','Tackles']],
  // Degen Crew tiles for NFL — the shell reads SPORT_CONFIG.crew and falls back to its
  // built-in AFL list when a sport doesn't supply one. Keys must match the branches in
  // nfl/signals.js renderTile().
  crew: [
    {k:'multi',    n:'Multi Builder',      i:'ti-stack-2',       d:'Stacked legs'},
    {k:'green',    n:'Green Lights',       i:'ti-traffic-lights',d:'Strong OVER'},
    {k:'paydirt',  n:'Tuddy Targets',      i:'ti-ball-american-football', d:'Redzone royalty'},
    {k:'elite',    n:'Elite Matchups',     i:'ti-trophy',        d:'Top v soft'},
    {k:'bunnies',  n:'Divisional Bunnies', i:'ti-carrot',        d:'Owns this rival'},
    {k:'death',    n:'Death Riders',       i:'ti-skull',         d:'Strong UNDER'},
    {k:'bogey',    n:'Divisional Bogey',   i:'ti-mood-sad',      d:'Struggles v rival'},
    {k:'streak',   n:'Streakers',          i:'ti-flame',         d:'Hot form'},
    {k:'chunk',    n:'Chunk Plays',        i:'ti-bolt',          d:'Big-play streaks'},
    {k:'wrap',     n:'Tackle Machines',    i:'ti-hammer',        d:'Tackle volume'},
    {k:'wx',       n:'Weather Watch',      i:'ti-wind',          d:'Conditions angles'},
    {k:'usage',    n:'Usage Trend',        i:'ti-activity',      d:'Role shifts'},
    {k:'next',     n:'Next Man Up',        i:'ti-user-plus',     d:'Injury volume'},
    {k:'stack',    n:'Stack Lab',          i:'ti-link',          d:'Correlated SGMs'},
    {k:'ledger',   n:'Signal Ledger',      i:'ti-notebook',      d:'Track record'},
    {k:'push',     n:'Playoff Push',       i:'ti-ladder',        d:'Standings pressure'},
    {k:'form',     n:'Form Alerts',        i:'ti-trending-up',   d:'Trend shifts'},
    {k:'clamp',    n:'Clamp Watch',        i:'ti-lock',          d:'Shadow CB risk'},
  ],
  posOrder: ['QB','RB','WR','TE','LB','DL','DB'],
  dvpStats: [['receptions','Receptions'],['targets','Targets'],['recYds','Rec Yds'],['recTds','Rec TDs'],['rushYds','Rush Yds'],['rushAtt','Rush Att'],['rushTds','Rush TDs'],['passYds','Pass Yds'],['passAtt','Pass Att'],['passTds','Pass TDs'],['passInt','INTs'],['rushRecYds','Rush+Rec'],['anytimeTd','Anytime TD'],['fanPts','Fan Pts'],['tackles','Tackles+Ast'],['soloTk','Solo Tackles'],['defSacks','Sacks Made']],
};
