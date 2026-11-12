/* The model's own picks, but only at short prices.
 *
 * Graded across every sport, the model's longshots are where the money goes: $6-15 ran at
 * -14.4% over 11,099 picks with a 90% interval of -28.2 to -2.9, while $1.60-2 ran at +4.1%.
 * Capping the whole set at $2 turned -2.3% and -1,224 units into +3.9% and +742.
 *
 * This is that, as a rule, so the cap can be re-measured whenever the archive grows rather than
 * taken on trust from one run. Set the cap with RULE_CAP (default 3.0).
 */
const CAP = +(process.env.RULE_CAP || 3.0);
module.exports = {
  name: `model picks under $${CAP.toFixed(2)}`,
  pick: ctx => (ctx.price <= CAP && (ctx.evCap >= 0.03 || ctx.evRaw >= 0.03)) ? ctx.side : null
};
