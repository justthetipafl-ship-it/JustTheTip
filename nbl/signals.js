/* JTT NBL signals module — shell interface shim.
   Matches the JTTSignals.create() contract { tiles, playStyles, collectOU, matchupLegs,
   captureOU, captureMatchup } so the shell boots cleanly. The basketball Degen engines
   (Slashers/Splashers/Dishers/Boarders/Bunnies/Bogey/Streakers/Form/etc.) port in Phase 2. */
window.JTTSignals = (function () {
  "use strict";
  function create(deps) {
    return {
      tiles: function () { return {}; },          // no signal tiles yet (Phase 2)
      playStyles: function () { return {}; },
      collectOU: function () { return []; },        // Green Lights / Death Riders
      matchupLegs: function () { return []; },       // Multi Builder / Déjà Vu legs
      captureOU: function () { return null; },
      captureMatchup: function () { return null; }
    };
  }
  return { create: create };
})();
