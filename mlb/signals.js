/* JTT MLB — signals.js  (window.JTTSignals)
   Degen tiles + OU/matchup collectors for the unified shell. Instantiated via
   JTTSignals.create(deps). NOTE: tile bodies are placeholders for now — the real
   MLB engines (Hot Bats, K Targets, Long Ball, Platoon Edge, Hittable Arms, Wheels)
   land in the next build step; the scoring core they'll use is already in scoring.js. */
window.JTTSignals = (function () {
  'use strict';
  function create(deps) {
    deps = deps || {};
    var emptyState = deps.emptyState || function () { return ''; };
    function ph(icon, title) { return emptyState(icon, title, 'MLB signal engine is being wired up.'); }

    var tiles = {
      hot: function () { return ph('ti-flame', 'Hot Bats'); },
      ktargets: function () { return ph('ti-target-arrow', 'K Targets'); },
      longball: function () { return ph('ti-ball-baseball', 'Long Ball'); },
      platoon: function () { return ph('ti-arrows-shuffle', 'Platoon Edge'); },
      coldarm: function () { return ph('ti-temperature', 'Hittable Arms'); },
      wheels: function () { return ph('ti-run', 'Wheels'); }
    };

    function playStyles(p, pg) { return [pg]; }
    function collectOU() { return []; }
    function matchupLegs() { return []; }
    function captureOU() { return null; }
    function captureMatchup() { return null; }

    return {
      tiles: tiles, playStyles: playStyles, collectOU: collectOU,
      matchupLegs: matchupLegs, captureOU: captureOU, captureMatchup: captureMatchup
    };
  }
  return { create: create };
})();
