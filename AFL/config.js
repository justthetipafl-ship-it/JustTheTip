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
  dvpsOpts: [['disposals','Disposals'],['kicks','Kicks'],['handballs','Handballs'],['marks','Marks'],['tackles','Tackles'],['clearances','Clearances'],['goals','Goals'],['dreamteam','Fantasy']],
  dvpStylePos: {'Accumulator Mid':'Midfielder','Contested Mid':'Midfielder','Running Mid':'Midfielder','Handball Mid':'Midfielder','Scoring MF':'Mid-Forward','Bear in the Square':'Key Forward','Key Target':'Key Forward','Pressure Fwd':'Gen. Forward','Small Forwards':'Gen. Forward','Clearance Beast':'Midfielder','Intercept Def':'Key Defender','Uncontested Marker':'Gen. Defender','Defensive Ball User':'Gen. Defender','Ruck Mid':'Ruck','Dominant Ruck':'Ruck','Winger':'Midfielder','Shankers':'All Positions','Cuddle Monsters':'All Positions','Fantasy Stars':'All Positions','Entry Man':'All Positions'},
  dvMarkets: [['disposals','Disposals',15],['kicks','Kicks',8],['handballs','Handballs',6],['marks','Marks',3],['tackles','Tackles',3],['clearances','Clearances',2],['goals','Goals',1]],
  roundWord: 'Round',
  betStats: [['disposals','Disposals'],['goals','Goals'],['marks','Marks'],['tackles','Tackles'],['kicks','Kicks'],['handballs','Handballs'],['clearances','Clearances']],
  dataFiles: { kickins:'data/kickins.json' },
  posOrder: ['Midfielder','Mid-Forward','Gen. Forward','Key Forward','Gen. Defender','Key Defender','Ruck'],
  dvpStats: [['disposals','Disposals'],['kicks','Kicks'],['handballs','Handballs'],['marks','Marks'],['tackles','Tackles'],['goals','Goals'],['shotsAtGoal','Shots'],['clearances','Clearances'],['inside50s','Inside 50s'],['contested','Cont. Poss'],['intercepts','Intercepts'],['hitouts','Hitouts'],['marks','Marks'],['groundBallGets','Ground Balls'],['metresGained','Metres'],['scoreInvolvements','Score Inv.']],
};
