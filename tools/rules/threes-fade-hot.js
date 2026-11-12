/* Threes under, fading a man shooting above his own rate lately.
 *
 * The mechanism is measured over 15,048 player-games: a shooter's recent three-point percentage
 * predicts his next game's at r=-0.0275, and the hottest fifth then shoot 1.9 points BELOW their
 * own rate. Mean reversion, and it points the opposite way to how a hot streak is usually read.
 *
 * The size is the problem. 1.9 points of 3P% on 4.2 attempts is 0.08 of a three, about 0.05 of a
 * standard deviation, which moves the win probability roughly 2.1 points - against the 2.6 you
 * need at $1.90. So the expected edge is about -0.5 points: real, and smaller than the juice.
 * It is here to be re-measured, not because it was expected to clear.
 *
 * RULE_HOT sets how far above his own rate counts as hot (default 5 points).
 */
const HOT = +(process.env.RULE_HOT || 0.05);
module.exports = {
  name: `threes under, fading a shooter ${Math.round(HOT * 100)}+ points hot`,
  markets: ['threes'],
  pick: ctx => {
    if (ctx.side !== 'under') return null;
    const f = ctx.data('threes');
    if (!f || !f.players) return null;
    const me = f.players.filter(p => p.name === ctx.player)[0];
    if (!me || me.pct == null) return null;
    const last = ctx.prior.slice(-5);
    if (last.length < 4) return null;
    const att = last.reduce((a, r) => a + (+r.threesAtt || 0), 0);
    if (att < 10) return null;                       // too few shots for a rate to mean anything
    const pct = last.reduce((a, r) => a + (+r.threes || 0), 0) / att;
    return (pct - me.pct) >= HOT ? 'under' : null;
  }
};
