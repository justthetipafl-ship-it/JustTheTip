/* JTT sport config - AFLW. Same game as AFL -> same markets/positions/engines. */
window.SPORT_CONFIG = {
  key: 'aflw',
  name: 'AFLW',
  noPlayerOdds: true,   // no AFLW player-prop odds -> hide Odds tab + Jupiter + Radar
  hideCrew: ['green','death','multi','radar','kickins','taggers'],   // odds-only / no-data / AFL-men's-list tiles -> hidden for AFLW
  jupiter: {
    markets: [['disposals','Disp'],['goals','Goals']],
    mlab:    { disposals:'disp', goals:'goals' },
    base:    { disposals:15, goals:1 },
  },
  mktNames: { disposals:'Disposals', goals:'Goals', dreamteam:'Fantasy', marks:'Marks', tackles:'Tackles', clearances:'Clearances', kicks:'Kicks', handballs:'Handballs' },
  pcStats:  [['disposals','Disp'],['goals','Goals'],['kicks','Kicks'],['handballs','HB'],['marks','Marks'],['tackles','Tackles'],['clearances','Clear'],['dreamteam','Fantasy']],
  cmbPick:  [['disposals','Disposals',15],['goals','Goals',0.5]],
  fxpMarkets: [['disposals','Disp'],['goals','Goals']],
  dvpsOpts: [['disposals','Disposals'],['kicks','Kicks'],['handballs','Handballs'],['marks','Marks'],['tackles','Tackles'],['clearances','Clearances'],['goals','Goals'],['dreamteam','Fantasy']],
  dvpStylePos: {'Accumulator Mid':'Midfielder','Contested Mid':'Midfielder','Running Mid':'Midfielder','Handball Mid':'Midfielder','Scoring MF':'Mid-Forward','Bear in the Square':'Key Forward','Key Target':'Key Forward','Pressure Fwd':'Gen. Forward','Small Forwards':'Gen. Forward','Clearance Beast':'Midfielder','Intercept Def':'Key Defender','Uncontested Marker':'Gen. Defender','Defensive Ball User':'Gen. Defender','Ruck Mid':'Ruck','Dominant Ruck':'Ruck','Winger':'Midfielder','Shankers':'All Positions','Cuddle Monsters':'All Positions','Fantasy Stars':'All Positions','Entry Man':'All Positions'},
  h2hKeys: [['disposals','Disposals'],['kicks','Kicks'],['handballs','Handballs'],['marks','Marks'],['contested','Contested'],['clearances','Clearances'],['inside50s','Inside 50s'],['tackles','Tackles'],['goals','Goals']],
  lowCount: ['goals'],
  dvpByPos: false,
  teamCols: [['disposals','Disp'],['kicks','Kick'],['handballs','HB'],['marks','Mark'],['tackles','Tkl'],['goals','Goal'],['shotsAtGoal','Shot'],['clearances','Clr'],['inside50s','I50'],['contestedMarks','CM'],['interceptMarks','IM'],['goalAssists','GA'],['scoreInvolvements','SI']],
  oddsMkts: [['disposals','Disposals'],['goals','Goals']],
  multiMkts: [['disposals','Disposals'],['goals','Goals']],   // AFLW: only disposals + goals props exist
  posColors: {'Midfielder':'#3b82f6','Mid-Forward':'#8b5cf6','Gen. Forward':'#f59e0b','Key Forward':'#f97316','Gen. Defender':'#64748b','Key Defender':'#06b6d4','Ruck':'#ec4899'},
  crew: [
    {k:'board',n:"Today's Board",i:'ti-clipboard-list',d:'Best plays ranked'},
    {k:'multi',n:'Multi Builder',i:'ti-stack-2',d:'Stacked legs'},
    {k:'green',n:'Green Lights',i:'ti-traffic-lights',d:'Strong OVER'},
    {k:'death',n:'Death Riders',i:'ti-skull',d:'Strong UNDER'},
    {k:'elite',n:'Elite Matchups',i:'ti-trophy',d:'Top v soft'},
    {k:'bunnies',n:'Bunnies',i:'ti-carrot',d:'Loves this opp'},
    {k:'bogey',n:'Bogey',i:'ti-mood-sad',d:'Struggles v opp'},
    {k:'snags',n:'Snags',i:'ti-ball-american-football',d:'Goalscoring'},
    {k:'streak',n:'Streakers',i:'ti-flame',d:'Hot form'},
    {k:'climb',n:"Let's Climb",i:'ti-stairs-up',d:'Ladder value'},
    {k:'hunting',n:'Hunting Grounds',i:'ti-target-arrow',d:'Venue feasters'},
    {k:'kickins',n:'Kick In Merchants',i:'ti-arrow-back-up',d:'Rebound / kick-in role'},
    {k:'nmu',n:'Next Man Up',i:'ti-arrow-up-right',d:'Beneficiaries of outs'},
    {k:'form',n:'Form Alerts',i:'ti-trending-up',d:'Trend shifts'},
    {k:'taggers',n:'Lurking Taggers',i:'ti-user-search',d:'Tag risk'},
  ],
  dvMarkets: [['disposals','Disposals',15],['goals','Goals',1]],
  roundWord: 'Round',
  betStats: [['disposals','Disposals'],['goals','Goals']],
  dataFiles: {},   // AFLW has no kickins.json; Kick In Merchants tile stays empty for v1
  posOrder: ['Midfielder','Mid-Forward','Gen. Forward','Key Forward','Gen. Defender','Key Defender','Ruck'],
  dvpStats: [['disposals','Disposals'],['kicks','Kicks'],['handballs','Handballs'],['marks','Marks'],['tackles','Tackles'],['goals','Goals'],['shotsAtGoal','Shots'],['clearances','Clearances'],['inside50s','Inside 50s'],['contested','Cont. Poss'],['intercepts','Intercepts'],['hitouts','Hitouts'],['marks','Marks'],['groundBallGets','Ground Balls'],['metresGained','Metres'],['scoreInvolvements','Score Inv.']],
};
