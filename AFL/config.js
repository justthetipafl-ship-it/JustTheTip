/* JTT sport config — AFL. Loaded by the unified shell as window.SPORT_CONFIG. */
window.SPORT_CONFIG = {
  key: 'afl',
  name: 'AFL',
  jupiter: {
    markets: [['disposals','Disp'],['goals','Goals'],['marks','Marks'],['tackles','Tackles'],['kicks','Kicks'],['handballs','HB'],['clearances','Clear']],
    mlab:    { disposals:'disp', goals:'goals', marks:'marks', tackles:'tackles', kicks:'kicks', handballs:'HB', clearances:'clears' },
    base:    { disposals:20, goals:1, marks:4, tackles:4, kicks:12, handballs:8, clearances:4 },
  },
  mktNames: { disposals:'Disposals', goals:'Goals', dreamteam:'Fantasy', marks:'Marks', tackles:'Tackles', clearances:'Clearances', kicks:'Kicks', handballs:'Handballs' },
  pcStats:  [['disposals','Disp'],['goals','Goals'],['kicks','Kicks'],['handballs','HB'],['marks','Marks'],['tackles','Tackles'],['clearances','Clear'],['dreamteam','Fantasy']],
  cmbPick:  [['disposals','Disposals',15],['kicks','Kicks',8],['handballs','Handballs',8],['marks','Marks',3],['tackles','Tackles',3],['clearances','Clearances',3],['goals','Goals',0.6],['dreamteam','Fantasy',75]],
  fxpMarkets: [['disposals','Disp'],['goals','Goals'],['marks','Marks'],['tackles','Tackles'],['clearances','Clear'],['kicks','Kicks'],['handballs','HB']],
  dataFiles: { kickins:'data/kickins.json' },
  posOrder: ['Midfielder','Mid-Forward','Gen. Forward','Key Forward','Gen. Defender','Key Defender','Ruck'],
  dvpStats: [['disposals','Disposals'],['kicks','Kicks'],['handballs','Handballs'],['marks','Marks'],['tackles','Tackles'],['goals','Goals'],['shotsAtGoal','Shots'],['clearances','Clearances'],['inside50s','Inside 50s'],['contested','Cont. Poss'],['intercepts','Intercepts'],['hitouts','Hitouts'],['marks','Marks'],['groundBallGets','Ground Balls'],['metresGained','Metres'],['scoreInvolvements','Score Inv.']],
};
