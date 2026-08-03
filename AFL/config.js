/* JTT sport config — AFL. Loaded by the unified shell as window.SPORT_CONFIG.
   Sport-specific settings live here so the shell framework stays sport-agnostic.
   (Migrating incrementally: Jupiter markets first; card/prop-calc/DVP/abbr config to follow.) */
window.SPORT_CONFIG = {
  key: 'afl',
  name: 'AFL',
  jupiter: {
    markets: [['disposals','Disp'],['goals','Goals'],['marks','Marks'],['tackles','Tackles'],['kicks','Kicks'],['handballs','HB'],['clearances','Clear']],
    mlab:    { disposals:'disp', goals:'goals', marks:'marks', tackles:'tackles', kicks:'kicks', handballs:'HB', clearances:'clears' },
    base:    { disposals:20, goals:1, marks:4, tackles:4, kicks:12, handballs:8, clearances:4 },
  },
};
