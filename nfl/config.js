/* JTT sport config — NFL. Loaded by the unified shell as window.SPORT_CONFIG. */
window.SPORT_CONFIG = {
  key: 'nfl',
  name: 'NFL',
  jupiter: {
    // keys match NFL's stat keys (same as CMB_PICK_MARKETS in the NFL module)
    markets: [['passYds','Pass Yd'],['rushYds','Rush Yd'],['recYds','Rec Yd'],['receptions','Rec'],['rushAtt','Rush Att'],['passTds','Pass TD'],['tackles','Tackles']],
    mlab:    { passYds:'pass yd', rushYds:'rush yd', recYds:'rec yd', receptions:'rec', rushAtt:'rush att', passTds:'pass TD', tackles:'tackles' },
    base:    { passYds:225, rushYds:45, recYds:45, receptions:4, rushAtt:12, passTds:1.5, tackles:6 },
  },
};
