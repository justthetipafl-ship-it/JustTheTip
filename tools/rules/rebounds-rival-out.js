/* Rebounds over, when a man who takes boards off him is not playing.
 *
 * The effect is real and measured: across 189 players, the top rebounder on a club sitting was
 * worth +0.86 rebounds against +0.21 for a fringe team-mate, and 73% of players went up.
 *
 * It is also mostly PRICED. Measured on the archive, when a rival is out the book moves the line
 * +0.27 against his season average while he actually moves +0.48 - so about +0.22 of a rebound
 * is left, which is roughly the width of the juice. This rule exists to keep checking that as
 * more days accumulate, not because it was expected to clear.
 *
 * RULE_REB sets how big a rival has to be (default 5 boards a game, eased).
 */
const MIN = +(process.env.RULE_REB || 5);
module.exports = {
  name: `rebounds over when a ${MIN}+ board rival sits`,
  markets: ['rebounds'],
  pick: ctx => {
    if (ctx.side !== 'over') return null;
    const f = ctx.data('rebounds');
    if (!f || !f.players) return null;
    const me = f.players.filter(p => p.name === ctx.player)[0];
    if (!me) return null;
    const out = (me.rivals || []).filter(v => v.reb >= MIN && ctx.absent.indexOf(v.name) >= 0);
    return out.length ? 'over' : null;
  }
};
