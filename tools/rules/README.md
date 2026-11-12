# Candidate rules

A rule is an idea for a tile, written as a few lines, so it can be graded against prices that
were really on the board **before** it ships. Run one with:

    node tools/grade-odds.js --rule tools/rules/<file>.js [--sport nba]

Every tile currently on the ledger was shipped first and measured afterwards. Two of them are
past -60% ROI. This directory exists so that stops happening.

A rule file exports:

```js
module.exports = {
  name: 'what it is',
  markets: ['rebounds'],          // optional; omit to see every market
  pick: ctx => 'over' | 'under' | null
};
```

`ctx` carries `sport`, `player`, `market`, `line`, `over`, `under`, `side`, `price`, `opp`,
`team`, `day`, `prior` (his games before this one, oldest first), `absent` (team-mates who did
not play), `data(file)` to read that sport's `data/<file>.json`, and `prob` / `evRaw` / `evCap`
from the model so a rule can filter the model rather than replace it.

`pick` is called once per side and must name the side it wants, so a rule that says `'under'`
is never also graded as an over.

## Reading the result

The report prints a 90% interval resampled **by game**, not by pick. A band whose interval spans
zero has not been shown to do anything, however good its ROI looks — on a few hundred picks an
ROI of +25% routinely means nothing. Treat `CLEARS` as the only positive result, and remember
that testing ten rules at 90% will hand you one `CLEARS` by chance.

## Two honest limits

`absent` comes from the gamelogs after the fact, so it includes late scratches a bettor could
not have known about and misses a man who was always going to rest. It flatters any
absence-based rule slightly.

And a rule tested on the same data that suggested it is not evidence. Form the idea on one
window, test it on another.
