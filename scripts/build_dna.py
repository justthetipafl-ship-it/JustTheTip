#!/usr/bin/env python3
"""Bet DNA: what actually separates a main-line prop that lands from one that does not.

    python3 scripts/build_dna.py nfl            # writes nfl/data/dna.json
    python3 scripts/build_dna.py --selftest

WHY IT IS BUILT THIS WAY, AND NOT THE OBVIOUS WAY
The obvious Bet DNA looks at ONE player's history and reports which conditions his hits happened
under. That cannot work, and it is worth writing down why so nobody rebuilds it.

A player has twenty to forty games on file. Ask thirty questions of thirty games and you will find
a pattern whether or not one exists. Measured against a simulated player who is a pure coin flip
with no edge whatsoever: the best-looking condition averages a 71-point separation, and something
at 80% or better turns up about a fifth of the time. All chance.

Meanwhile the effects that are REAL, measured by pooling 321 receivers over 7,290 games, are about
THREE POINTS: at home +3.1, against a top-ten defence -3.1, team won +0.1 (ie nothing). A
three-point effect cannot be seen in thirty games - the noise floor there is fifty to seventy.
So a per-player strand search is guaranteed to surface chance and bury the truth.

The fix is to measure each strand where there is enough evidence to measure it - across every
main-line bet in the league - and then profile one bet against those numbers. Small, true, and
they stack. That is what this builds.

MAIN LINES ONLY
A strand is measured on the line the books actually posted, taken from the odds-history snapshots,
not on a line reconstructed afterwards. That keeps the population the same one a reader bets into.
It also means the usable history starts when the snapshots did.

WHAT IT CANNOT SEE
  * dvp.json is today's defensive ranking, not the ranking as it stood in week 2. Matchup strands
    use it as a stand-in and are marked so.
  * Weather is only published for the current slate, so there is no historical weather strand.
  * A strand is only reported when its sample can support it; everything carries its own n and
    standard error so a thin one is visibly thin rather than quietly wrong.
"""
import argparse
import collections
import datetime
import gzip
import json
import math
import os
import sys

SPORT_DIR = {'afl': 'AFL', 'nfl': 'nfl', 'epl': 'EPL', 'mlb': 'mlb', 'nbl': 'nbl', 'nhl': 'nhl',
             'nba': 'nba'}
# the market's value does not always share its name with the gamelog column
MKT_FIELD = {'nfl': {'sacks': 'defSacks', 'anytimeTd': 'totalTds'}}
# usage columns that DRIVE a market rather than describing the game around it. "He goes over his
# receiving yards when he gets more targets" is arithmetic; it is reported, but labelled.
MECHANICAL = {
    'nfl': {'recYds': ['targets', 'tgtShare', 'airYds', 'snapPct', 'sep', 'rzTgt_g'],
            'receptions': ['targets', 'tgtShare', 'airYds', 'snapPct', 'sep'],
            'recTds': ['targets', 'rzTgt_g', 'snapPct'],
            'rushYds': ['rushAtt', 'snapPct'], 'rushTds': ['rushAtt', 'rzAtt_g', 'snapPct'],
            'passYds': ['passAtt', 'snapPct'], 'passTds': ['passAtt', 'snapPct'],
            'passComp': ['passAtt', 'snapPct'],
            'rushRecYds': ['rushAtt', 'targets', 'tgtShare', 'snapPct'],
            'anytimeTd': ['rzTgt_g', 'rzAtt_g', 'snapPct', 'targets', 'rushAtt']},
}
UNIVERSAL_MECH = ('snapPct', 'minutes', 'toiMin', 'min', 'tog')
CLOSE_BY = {'nfl': 7, 'nba': 6, 'nbl': 6, 'nhl': 1, 'mlb': 2, 'afl': 12, 'epl': 1}
# usage columns worth asking about, per sport
USAGE = {
    # NOT the _g columns (rzTgt_g, rzAtt_g, i10Tgt_g...). The suffix means per-game SEASON RATE,
    # so the value is the same on every one of a player's rows - Adam Thielen has two distinct
    # values of rzTgt_g across 26 games. "Above his usual" then separates players who have a
    # red-zone role from players who do not, which is a measure of who he is and not of what
    # happened in the game. It scored a convincing +12.5 points that way, which is exactly the
    # kind of confound this whole table exists to avoid.
    'nfl': [('snapPct', 'Snap share'), ('tgtShare', 'Target share'), ('targets', 'Targets'),
            ('airYds', 'Air yards'), ('rushAtt', 'Carries'),
            ('passAtt', 'Pass attempts'), ('sep', 'Separation'), ('cushion', 'Cushion'),
            ('boxCount', 'Men in the box'), ('aDotNgs', 'Average depth of target'),
            ('airShare', 'Air-yard share'), ('cpoe', 'Completion % over expected')],
    'nba': [('minutes', 'Minutes'), ('fga', 'Shot attempts')],
    'nbl': [('minutes', 'Minutes'), ('fga', 'Shot attempts')],
    'nhl': [('toiMin', 'Time on ice'), ('shots', 'Shots')],
    'epl': [('min', 'Minutes'), ('shots', 'Shots')],
    'afl': [('tog', 'Time on ground'), ('cba', 'Centre bounces')],
    'mlb': [],
}


def load(path, default=None):
    try:
        if path.endswith('.gz'):
            with gzip.open(path, 'rt', encoding='utf-8') as fh:
                return json.load(fh)
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def num(v):
    if v is None or isinstance(v, bool) or v == '':
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f or f in (float('inf'), float('-inf')) else f


def gamelogs_for(root, sport):
    base = os.path.join(root, SPORT_DIR.get(sport, sport), 'data')
    rows = load(os.path.join(base, 'gamelogs.json'))
    out = list(rows) if isinstance(rows, list) else []
    meta = load(os.path.join(base, 'meta.json'), {}) or {}
    for name in (meta.get('gamelogFiles') or []):
        part = load(os.path.join(base, name))
        if isinstance(part, list):
            out.extend(part)
    return out


def main_lines(root, sport):
    """One posted line per (season, week, player, market), with how it moved before kickoff.

    The snapshots are daily, so the same line is seen many times. The LAST sighting before the
    games is the main line a reader would have bet into; the first is where it opened. The gap
    between them is the market's own opinion, which is usually the strongest strand there is.
    """
    d = os.path.join(root, SPORT_DIR.get(sport, sport), 'odds-history')
    if not os.path.isdir(d):
        return {}
    out = {}
    for fn in sorted(os.listdir(d)):
        if not (fn.endswith('.json') or fn.endswith('.json.gz')):
            continue
        snap = load(os.path.join(d, fn))
        if not isinstance(snap, dict):
            continue
        fx = [g for g in (snap.get('fixture') or []) if isinstance(g, dict) and g.get('week')]
        if not fx:
            continue
        week = min(g['week'] for g in fx)
        season = min((g.get('season') or 0) for g in fx) or None
        # A SNAPSHOT CARRIES THE WHOLE ALT LADDER - up to 32 rungs for one player and market,
        # from 224.5 to 424.5 passing yards. The main line is ONE of them: the two-way rung
        # priced closest to even money, which is the one the book is actually balancing.
        # The first version of this took `line` from whichever row came last and `over`/`under`
        # from whichever happened to carry them, so a line off one rung was being graded against
        # prices off another. It left the middle of the market calibrated to within a point and
        # the tails out by twenty, which is what a mixed-up ladder looks like and nothing like a
        # real market inefficiency. A line and its prices now always travel together.
        best = {}
        for r in (snap.get('odds') or []):
            if not isinstance(r, dict):
                continue
            p, m = r.get('player'), r.get('market')
            line, ov, un = num(r.get('line')), num(r.get('over')), num(r.get('under'))
            if not p or not m or line is None or not ov or not un or ov <= 1 or un <= 1:
                continue                            # one-sided rungs cannot be de-vigged
            bal = abs((1 / ov) - (1 / un))          # 0 = perfectly balanced = the main line
            k = (p, m)
            if k not in best or bal < best[k][0]:
                best[k] = (bal, line, ov, un)
        for (p, m), (_, line, ov, un) in best.items():
            k = (season, week, p, m)
            e = out.get(k)
            if e is None:
                out[k] = {'open': line, 'line': line, 'openOver': ov,
                          'over': ov, 'under': un, 'n': 1}
            else:
                e['line'], e['over'], e['under'] = line, ov, un   # later snapshot wins, as a set
                e['n'] += 1
    return out


def stat_of(sport, row, market, fields):
    """Mirrors the page, loosely: a market's value on a gamelog row. Missing is missing here -
    this is analysis, not settlement, so there is no cover-field zero to argue about."""
    market = MKT_FIELD.get(sport, {}).get(market, market)
    if fields and market not in fields:
        return None
    return num(row.get(market))


def wilson(h, n):
    """A hit rate with an honest interval. A raw rate on nine games reads like one on nine hundred;
    this is what stops a thin strand looking the same as a thick one on the page."""
    if not n:
        return (0.0, 0.0, 0.0)
    z = 1.96
    p = h / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (p, max(0.0, centre - half), min(1.0, centre + half))


def build(root, sport, min_n=60):
    base = os.path.join(root, SPORT_DIR.get(sport, sport), 'data')
    lines = main_lines(root, sport)
    if not lines:
        return {'sport': sport, 'error': 'no odds-history to measure main lines from',
                'strands': [], 'n': 0}
    logs = gamelogs_for(root, sport)
    fields = set()
    for r in logs[:4000]:
        fields.update(r.keys())
    by_pw = {}
    for r in logs:
        p = r.get('Player')
        if not p:
            continue
        by_pw[(num(r.get('Year')), num(r.get('Week')), p)] = r

    results = load(os.path.join(base, 'results.json'), []) or []
    score = {}
    for r in results:
        if isinstance(r, dict) and r.get('home') and r.get('away'):
            k = (num(r.get('season')), num(r.get('week')))
            score[(k, str(r['home']).upper())] = (num(r.get('hs')), num(r.get('as')))
            score[(k, str(r['away']).upper())] = (num(r.get('as')), num(r.get('hs')))

    dvp = load(os.path.join(base, 'dvp.json'), []) or []
    dvp_rank = {}
    for mkt_rows in (dvp if isinstance(dvp, list) else []):
        if isinstance(mkt_rows, dict) and mkt_rows.get('team'):
            dvp_rank[(str(mkt_rows.get('pos') or 'ALL'), str(mkt_rows['team']).upper())] = \
                num(mkt_rows.get('val'))

    players = load(os.path.join(base, 'players.json'), []) or []
    pos_of = {p['name']: (p.get('position') or p.get('pos') or 'ALL')
              for p in players if isinstance(p, dict) and p.get('name')}

    # ---- grade every main line, and record every strand value alongside -----------------------
    close_by = CLOSE_BY.get(sport, 7)
    usage_cols = [u for u in USAGE.get(sport, []) if u[0] in fields]
    # a player's own median per usage column, so "above his usual" means his
    own = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in logs:
        p = r.get('Player')
        if not p:
            continue
        for col, _ in usage_cols:
            v = num(r.get(col))
            if v is not None:
                own[p][col].append(v)
    # a column with one or two distinct values for a player is a season rate wearing a per-game
    # name; it cannot describe what happened in a given game, so it gets no median and drops out
    med = {p: {c: sorted(v)[len(v) // 2] for c, v in cols.items()
               if len(v) >= 6 and len(set(v)) >= 4}
           for p, cols in own.items()}

    rows = []
    for (season, week, player, market), od in lines.items():
        row = by_pw.get((num(season), num(week), player))
        if row is None:
            continue
        v = stat_of(sport, row, market, fields)
        if v is None:
            continue
        line = od['line']
        hit = v > line                              # main lines are graded as the over
        team = str(row.get('Team') or '').upper()
        sc = score.get(((num(season), num(week)), team))
        marg = (sc[0] - sc[1]) if (sc and sc[0] is not None and sc[1] is not None) else None
        tot = (sc[0] + sc[1]) if (sc and sc[0] is not None and sc[1] is not None) else None
        pos = pos_of.get(player, 'ALL')
        dv = dvp_rank.get((pos, str(row.get('Opp') or '').upper()))
        # what the price implied, with the usual two-way overround taken out where both sides
        # are posted. Without de-vigging every bet looks like a small loser and every strand
        # inherits the same offset.
        # TWO-WAY LINES ONLY. De-vigging needs both sides; without an under the best you can do
        # is 1/over, which still has the margin in it and overstates the chance. That would be a
        # harmless constant if it fell evenly, but it does not: in the NFL sample 5,748 longshots
        # posted no under against 20 short prices, so the two price bands were being scored on
        # different rulers. The result was a confident, entirely false "favourites beat their
        # price by 17 points". A one-sided line is dropped rather than guessed at.
        ov, un = od.get('over'), od.get('under')
        implied = None
        if ov and un and ov > 1 and un > 1:
            implied = (1 / ov) / ((1 / ov) + (1 / un))
        rows.append({
            'edge': None if implied is None else (1.0 if hit else 0.0) - implied,
            'implied': implied,
            'hit': hit, 'market': market, 'player': player,
            'home': None if row.get('home') is None else bool(num(row.get('home'))),
            'marg': marg, 'tot': tot, 'dvp': dv,
            'over': od.get('over'), 'openOver': od.get('openOver'),
            'line': line, 'open': od.get('open'),
            'usage': {c: (num(row.get(c)), (med.get(player) or {}).get(c)) for c, _ in usage_cols},
        })
    if not rows:
        return {'sport': sport, 'error': 'no main line could be matched to a played game',
                'strands': [], 'n': 0}

    tot_med = sorted([r['tot'] for r in rows if r['tot'] is not None])
    tot_mid = tot_med[len(tot_med) // 2] if len(tot_med) >= 20 else None

    # ---- the strands -------------------------------------------------------------------------
    def S(sid, label, when, fn, mech=False, note=None):
        return {'id': sid, 'label': label, 'when': when, 'fn': fn, 'mech': mech, 'note': note}

    strands = [
        S('home', 'At home', 'before', lambda r: r['home']),
        S('dvp_soft', 'Opponent soft against his position', 'before',
          lambda r: None if r['dvp'] is None else r['dvp'] > 0,
          note='ranking is current, not as it stood that week'),
        S('short_price', 'Priced short (under $1.80)', 'before',
          lambda r: None if r['over'] is None else r['over'] < 1.80),
        S('long_price', 'Priced long (over $2.50)', 'before',
          lambda r: None if r['over'] is None else r['over'] > 2.50),
        S('line_up', 'Line moved up before kickoff', 'before',
          lambda r: None if (r['open'] is None or r['line'] is None) else r['line'] > r['open']),
        S('line_down', 'Line moved down before kickoff', 'before',
          lambda r: None if (r['open'] is None or r['line'] is None) else r['line'] < r['open']),
        S('price_short', 'Price shortened before kickoff', 'before',
          lambda r: None if (r['openOver'] is None or r['over'] is None)
          else r['over'] < r['openOver'] - 0.01),
        S('price_drift', 'Price drifted before kickoff', 'before',
          lambda r: None if (r['openOver'] is None or r['over'] is None)
          else r['over'] > r['openOver'] + 0.01),
        S('won', 'His team won', 'after', lambda r: None if r['marg'] is None else r['marg'] > 0),
        S('close', 'Close game', 'after',
          lambda r: None if r['marg'] is None else abs(r['marg']) <= close_by),
        S('blowout', 'Blowout either way', 'after',
          lambda r: None if r['marg'] is None else abs(r['marg']) >= close_by * 2),
        S('shootout', 'High-scoring game', 'after',
          lambda r: None if (r['tot'] is None or tot_mid is None) else r['tot'] > tot_mid),
        S('trailed', 'Trailed on the scoreboard', 'after',
          lambda r: None if r['marg'] is None else r['marg'] < 0),
    ]
    for col, label in usage_cols:
        mech = col in UNIVERSAL_MECH
        strands.append(S('u_' + col, label + ' above his usual', 'after',
                         (lambda c: lambda r: (None if (r['usage'].get(c) is None
                                                        or r['usage'][c][0] is None
                                                        or r['usage'][c][1] is None)
                                               else r['usage'][c][0] > r['usage'][c][1]))(col),
                         mech=mech))

    def measure(subset, label):
        """A strand's effect AGAINST THE PRICE, not against the raw hit rate.

        Measuring raw hit rates across main lines answers a question nobody asked. The first
        build of this table reported "priced under $1.80 hits 73%, priced over $2.50 hits 3.5%",
        which is not a discovery about football - it is the definition of a price. Worse, pooling
        a 6%-base market like first scorer with a 50% one like receptions means any strand that
        merely correlates with WHICH market it is picks up the difference and wears it as an edge.

        The honest question is whether a strand tells you something THE BOOK HAS NOT ALREADY PUT
        IN THE LINE. So every bet is scored as hit minus the price's own implied probability, and
        a strand is measured on that residual. Zero means the market had it covered. Positive
        means the strand beats the posted price, which is the only kind of strand worth betting.

        The proof this was needed: measured against each player's own median, being at home was
        worth +3.1 points. Measured against the line the book actually posted, it is worth -0.2
        and not significant - because the book prices home advantage already.
        """
        out = []
        for st in strands:
            ins, outs = [], []
            inn = inh = outn = outh = 0
            for r in subset:
                if r.get('edge') is None:
                    continue
                t = st['fn'](r)
                if t is None:
                    continue
                if t:
                    ins.append(r['edge']); inn += 1; inh += r['hit']
                else:
                    outs.append(r['edge']); outn += 1; outh += r['hit']
            if inn < 25 or outn < 25:
                continue
            pin, lo_in, hi_in = wilson(inh, inn)
            pout, _, _ = wilson(outh, outn)
            # the strand's effect on the residual, and whether that effect could be chance
            mi, mo = sum(ins) / inn, sum(outs) / outn
            vi = sum((x - mi) ** 2 for x in ins) / max(1, inn - 1)
            vo = sum((x - mo) ** 2 for x in outs) / max(1, outn - 1)
            se = math.sqrt(vi / inn + vo / outn)
            lift = mi - mo
            z = lift / se if se else 0
            mech = st['mech']
            if not mech and st['id'].startswith('u_'):
                mech = st['id'][2:] in (MECHANICAL.get(sport, {}).get(label) or []) \
                    if label != 'ALL' else False
            out.append({'id': st['id'], 'label': st['label'], 'when': st['when'],
                        'mechanical': bool(mech), 'note': st.get('note'),
                        'inN': inn, 'inHit': round(pin, 4), 'inLo': round(lo_in, 4),
                        'inHi': round(hi_in, 4), 'outN': outn, 'outHit': round(pout, 4),
                        # edge = how far the bets inside this strand beat their own price
                        'inEdge': round(mi, 4), 'outEdge': round(mo, 4),
                        'lift': round(lift, 4), 'z': round(z, 2),
                        'real': abs(z) >= 2.5})
        out.sort(key=lambda x: -abs(x['z']))
        return out

    doc = {'sport': sport,
           'built': datetime.datetime.now(datetime.timezone.utc).replace(
               tzinfo=None).isoformat(timespec='seconds') + 'Z',
           'n': len(rows), 'base': round(sum(r['hit'] for r in rows) / len(rows), 4),
           'all': measure(rows, 'ALL'), 'markets': {}}
    per = collections.defaultdict(list)
    for r in rows:
        per[r['market']].append(r)
    for mkt, sub in sorted(per.items()):
        if len(sub) < min_n:
            continue
        doc['markets'][mkt] = {'n': len(sub),
                               'base': round(sum(r['hit'] for r in sub) / len(sub), 4),
                               'strands': measure(sub, mkt)}
    return doc


def selftest():
    fails = []

    def chk(name, cond, detail=''):
        print(('  ok   ' if cond else '  FAIL ') + name + ('  ' + detail if detail else ''))
        if not cond:
            fails.append(name)

    p, lo, hi = wilson(5, 10)
    chk('a rate on ten games carries a wide interval', hi - lo > 0.4, '(%.2f wide)' % (hi - lo))
    p2, lo2, hi2 = wilson(500, 1000)
    chk('the same rate on a thousand carries a narrow one', hi2 - lo2 < 0.07,
        '(%.3f wide) - which is the whole point of showing it' % (hi2 - lo2))
    chk('a rate is still the rate', abs(p - 0.5) < 1e-9 and abs(p2 - 0.5) < 1e-9)
    chk('an empty sample does not divide by zero', wilson(0, 0) == (0.0, 0.0, 0.0))
    chk('num() rejects what JS would call non-finite',
        num('') is None and num(None) is None and num(True) is None and num('7') == 7)
    print('\nselftest: ' + ('FAILED - ' + ', '.join(fails) if fails else 'ALL PASS ok'))
    return 1 if fails else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('sports', nargs='*', default=['nfl'])
    ap.add_argument('--root', default='.')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--print', action='store_true', help='show the table instead of writing it')
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    for sp in (a.sports or ['nfl']):
        doc = build(a.root, sp)
        if a.print:
            print('%s: %d graded main lines, base %.1f%%'
                  % (sp, doc.get('n', 0), 100 * doc.get('base', 0)))
            print('   %-42s %9s %9s %10s %8s' % ('strand', 'inside', 'outside', 'v price', 'z'))
            for s in doc.get('all', [])[:40]:
                print('   %-42s %5.1f%%(%4d) %5.1f%%(%4d) %+6.1f pts  z=%+6.2f %s%s'
                      % (s['label'], 100 * s['inEdge'], s['inN'], 100 * s['outEdge'], s['outN'],
                         100 * s['lift'], s['z'], 'REAL ' if s['real'] else '     ',
                         '[mech]' if s['mechanical'] else '[' + s['when'] + ']'))
        else:
            out = os.path.join(a.root, SPORT_DIR.get(sp, sp), 'data', 'dna.json')
            with open(out, 'w', encoding='utf-8') as fh:
                json.dump(doc, fh, separators=(',', ':'))
            print('wrote %s  (%d graded main lines, %d strands)'
                  % (out, doc.get('n', 0), len(doc.get('all', []))))
