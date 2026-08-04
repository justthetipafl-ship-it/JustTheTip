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
  dataFiles: { firsttd:'data/firsttd.json', redzone:'data/redzone.json', dbs:'data/dbs.json' },
  posOrder: ['QB','RB','WR','TE','LB','DL','DB'],
  dvpStats: [['receptions','Receptions'],['targets','Targets'],['recYds','Rec Yds'],['recTds','Rec TDs'],['rushYds','Rush Yds'],['rushAtt','Rush Att'],['rushTds','Rush TDs'],['passYds','Pass Yds'],['passAtt','Pass Att'],['passTds','Pass TDs'],['passInt','INTs'],['rushRecYds','Rush+Rec'],['anytimeTd','Anytime TD'],['fanPts','Fan Pts'],['tackles','Tackles+Ast'],['soloTk','Solo Tackles'],['defSacks','Sacks Made']],
};
