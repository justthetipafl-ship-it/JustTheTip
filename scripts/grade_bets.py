#!/usr/bin/env python3
"""Settle the published bet ledger from the game logs.

    python3 scripts/grade_bets.py                  # grade data/results.json in place
    python3 scripts/grade_bets.py --dry-run        # say what would change, write nothing
    python3 scripts/grade_bets.py --selftest       # synthetic cases, no repo data needed

WHY THIS EXISTS
A tipped record is only worth looking at if its losers appear in it, and the quickest way to lose
that is a human deciding which bets are settled. The page already grades a running tip from the
game logs and shows it on the card - but that is computed in the browser and written nowhere, so
data/results.json kept every bet at 'pending' for ever and the published record never moved.
Nothing else writes that file: only the entry tool, by hand, and the entry tool has no screen for
settling a bet it already saved. So this runs after each data build and closes the loop.

WHAT IT WILL AND WILL NOT DECIDE
It writes only outcomes that are unambiguous, and leaves everything else pending with a reason
printed, because a wrong settlement in a published record is far worse than a slow one:

  * every leg landed                  -> win, returned = stake x odds
  * any leg beaten                    -> loss, returned = 0 (the rest cannot save it)
  * a SINGLE that pushed or voided    -> void, stake refunded
  * a MULTI with a void leg           -> LEFT PENDING. The book dropped that leg and re-priced
                                         the ticket, and we do not know the new price; claiming
                                         a win at odds nobody was paid is the one thing a record
                                         must never do.
  * a player not in the game logs     -> LEFT PENDING, and named. A name that does not match is
                                         the single most likely reason a bet never settles.
  * a leg with no game to point at    -> LEFT PENDING, and named.

A row that is already settled is never touched, whatever the logs say now.

KEEPING IT HONEST
These rules also exist in JavaScript, in the page, so a reader can see a tip grade itself live.
Two implementations of one rule set is exactly how a record ends up saying "won" on the page and
"loss" in the ledger, so tests/grader_test.js runs both over the same bets and the same game logs
and fails on any disagreement. Change a rule here and that test tells you to change it there.
"""
import argparse
import json
import os
import sys

# a sport missing here is never graded, so its bets sit pending for ever
SPORT_DIR = {'afl': 'AFL', 'nfl': 'nfl', 'epl': 'EPL', 'mlb': 'mlb', 'nbl': 'nbl', 'nhl': 'nhl',
             'nba': 'nba'}

# --- these three tables mirror index.html exactly; grader_test.js is what keeps them in step ---
# a posted market does not always share its name with the gamelog column
MKT_FIELD = {'nfl': {'sacks': 'defSacks', 'anytimeTd': 'totalTds'}}
# a row whose minutes say he did not play is not a game, whatever else it carries
PLAY_GATE = {'epl': 'min', 'afl': 'tog', 'nbl': 'minutes', 'nhl': 'toiMin'}
# proof he was on the field, so an absent stat reads as a real zero rather than a missing one
COVER_FIELD = {'epl': {'field': 'passes', 'min': None}, 'nfl': {'field': 'snapPct', 'min': 0.1}}


def load(path, default=None):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def num(v):
    """The float of v, or None - mirroring JS isFinite, where '' and bools are not numbers."""
    if v is None or isinstance(v, bool) or v == '':
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f or f in (float('inf'), float('-inf')) else f


def gamelogs_for(root, sport):
    """Every gamelog row for a sport, whether it ships one file or one per season."""
    base = os.path.join(root, SPORT_DIR.get(sport, sport), 'data')
    rows = load(os.path.join(base, 'gamelogs.json'))
    out = list(rows) if isinstance(rows, list) else []
    meta = load(os.path.join(base, 'meta.json'), {}) or {}
    for name in (meta.get('gamelogFiles') or []):
        part = load(os.path.join(base, name))
        if isinstance(part, list):
            out.extend(part)
    if not out:
        # some sports ship only per-season files and no meta list
        try:
            for fn in sorted(os.listdir(base)):
                if fn.startswith('gamelogs') and fn.endswith('.json') and fn != 'gamelogs.json':
                    part = load(os.path.join(base, fn))
                    if isinstance(part, list):
                        out.extend(part)
        except OSError:
            pass
    return out


def index_logs(rows):
    """Game logs by player, de-duplicated per game, plus the columns this sport actually carries.

    Both halves mirror the page's buildIndexes. The dedupe matters because NHL's build emits each
    game three times; the column list matters because a market the logs never carry must read as
    unverifiable rather than as a confident zero (see stat_of).
    """
    by_name, fields, seen = {}, set(), set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = r.get('Player') or r.get('player')
        if not name:
            continue
        gid = r.get('MatchId')
        if gid is None:
            gid = r.get('matchId')
        if gid is None:
            gid = str(r.get('Date') or r.get('date') or '') or None
        if gid is not None and gid != '':
            key = (name, str(gid))
            if key in seen:
                continue
            seen.add(key)
        by_name.setdefault(name, []).append(r)
        fields.update(r.keys())
    return by_name, fields


def stat_of(sport, row, market, fields):
    """The market's value on one gamelog row. Mirrors statOf() in index.html."""
    market = MKT_FIELD.get(sport, {}).get(market, market)
    gate = PLAY_GATE.get(sport)
    if gate and row.get(gate) is not None:
        g = num(row.get(gate))
        if g is not None and g <= 0:
            return None                       # on the sheet, did not take the field
    v = num(row.get(market))
    if v is not None:
        return v
    cover = COVER_FIELD.get(sport)
    if not cover:
        return None
    # a market this sport's logs do not carry at all is UNVERIFIABLE, never a zero: on an over a
    # confident zero is a silent loss on a bet nothing could check
    if fields and market not in fields:
        return None
    cv = num(row.get(cover['field']))
    if cv is None:
        return None
    if cover.get('min') is not None and cv < cover['min']:
        return None
    return 0.0


def leg_row(leg, by_name, fields):
    """Which gamelog row this leg is about. Mirrors betLegRow() in index.html."""
    if not by_name:
        return ('nodata', None)
    logs = by_name.get(leg.get('player'))
    if not logs:
        return ('noplayer', None)
    want_id = leg.get('matchId')
    want_id = None if want_id is None else str(want_id)
    want_date = str(leg.get('date'))[:10] if leg.get('date') else None
    want_opp = str(leg.get('opp')).upper() if leg.get('opp') else None
    dated = False
    for r in logs:
        if want_id is not None:
            if str(r.get('MatchId')) == want_id:
                return ('played', r)
            continue
        d = str(r.get('Date') or r.get('date') or '')[:10]
        if d:
            dated = True
        if not want_date or d != want_date:
            continue
        if want_opp and str(r.get('Opp') or r.get('opp') or '').upper() != want_opp:
            continue
        return ('played', r)
    if want_id is None and (not want_date or not dated):
        return ('unpinned', None)             # nothing to pin it to: it can never settle
    return ('notyet', None)


def settle_leg(leg, data):
    """One leg's outcome. Mirrors betSettleLeg() in index.html."""
    sport = str(leg.get('sport') or '').lower()
    if sport not in SPORT_DIR:
        return {'res': 'pending', 'why': 'nodata'}
    by_name, fields = data(sport)
    state, row = leg_row(leg, by_name, fields)
    if state != 'played':
        return {'res': 'pending', 'why': state}
    # TWO VERY DIFFERENT REASONS A STAT READS AS NOTHING, and they do not settle the same way:
    #   he was named but did not take the field  -> a VOID, which books refund. Correct.
    #   the logs do not carry this market at all -> we do not know what happened, so refunding it
    #                                               publishes a neutral result for a bet that had
    #                                               a real outcome. That is a wrong answer, not a
    #                                               cautious one, so it is HELD for a human.
    field = MKT_FIELD.get(sport, {}).get(leg.get('market'), leg.get('market'))
    if fields and field not in fields:
        return {'res': 'pending', 'why': 'unverifiable', 'have': None}
    v = stat_of(sport, row, leg.get('market'), fields)
    if v is None:
        return {'res': 'void', 'why': 'didnotplay', 'have': None}
    line = num(leg.get('line'))
    if line is None:
        return {'res': 'pending', 'why': 'noline', 'have': v}
    if v == line:
        return {'res': 'void', 'why': 'push', 'have': v}
    over = str(leg.get('side') or 'over') != 'under'
    won = v > line if over else v < line
    return {'res': 'win' if won else 'loss', 'have': v}


def settle(bet, data):
    """A whole bet. Mirrors betSettle() in index.html; returns None for a bet with no legs."""
    legs = bet.get('legs') or []
    if not legs:
        return None
    out = [settle_leg(l, data) for l in legs]
    pending = [x for x in out if x['res'] == 'pending']
    lost = [x for x in out if x['res'] == 'loss']
    voided = [x for x in out if x['res'] == 'void']
    if lost:
        return {'result': 'loss', 'legs': out, 'ret': 0}
    if pending:
        return {'result': 'pending', 'legs': out, 'open': len(pending)}
    if voided:
        stake = num(bet.get('stake'))
        if len(legs) == 1:
            return {'result': 'void', 'legs': out,
                    'ret': round(stake, 2) if stake is not None else None}
        return {'result': 'review', 'legs': out,
                'why': '%d leg%s voided - the book will have re-priced this, so it needs settling '
                       'by hand' % (len(voided), '' if len(voided) == 1 else 's')}
    odds, stake = num(bet.get('odds')), num(bet.get('stake'))
    return {'result': 'win', 'legs': out,
            'ret': round(stake * odds, 2) if (odds is not None and stake is not None) else None}


WHY_TEXT = {
    'noplayer': 'that name is not in the game logs, so this bet can never settle itself',
    'unpinned': 'the leg names no game, so it can never settle itself',
    'nodata':   'no game logs loaded for that sport',
    'notyet':   'the game has not been played yet',
    'noline':   'the leg has no line',
    'unverifiable': 'the game logs do not carry that market, so it cannot be graded here',
}


def grade(root, path, dry_run=False, quiet=False):
    """Settle what can be settled. Returns (changed, held) counts."""
    doc = load(path)
    if doc is None:
        print('no ledger at %s - nothing to grade' % path)
        return 0, 0
    bets = doc if isinstance(doc, list) else (doc.get('bets') or [])
    cache = {}

    def data(sport):
        if sport not in cache:
            cache[sport] = index_logs(gamelogs_for(root, sport))
        return cache[sport]

    changed, held, notes = 0, 0, []
    for b in bets:
        if not isinstance(b, dict) or b.get('result') != 'pending':
            continue                          # a settled row is never re-graded
        if not (b.get('legs') or []):
            held += 1
            notes.append('%s  held: typed as free text, so it has to be settled by hand'
                         % (str(b.get('bet'))[:48]))
            continue
        s = settle(b, data)
        if s is None or s['result'] in ('pending', 'review'):
            held += 1
            if s and s['result'] == 'review':
                notes.append('%s  HELD: %s' % (str(b.get('bet'))[:48], s.get('why')))
            else:
                whys = {x.get('why') for x in (s or {}).get('legs', []) if x['res'] == 'pending'}
                loud = [w for w in whys if w in ('noplayer', 'unpinned', 'noline',
                                                  'unverifiable')]
                if loud:
                    notes.append('%s  HELD: %s'
                                 % (str(b.get('bet'))[:48],
                                    '; '.join(WHY_TEXT.get(w, w) for w in sorted(loud))))
            continue
        b['result'] = s['result']
        if s.get('ret') is not None:
            b['returned'] = s['ret']
        # The dashboard is in UNITS and prefers a stored returnedUnits over re-deriving one, which
        # is what every imported row carries. Writing it keeps a graded row the same shape as the
        # rest of the ledger instead of leaning on the page to work it out. Same number either
        # way - units x odds for a winner, the stake back for a void, nothing for a loser.
        u = num(b.get('units'))
        if u is not None:
            odds = num(b.get('odds'))
            if s['result'] == 'win' and odds is not None:
                b['returnedUnits'] = round(u * odds, 4)
            elif s['result'] == 'void':
                b['returnedUnits'] = round(u, 4)
            else:
                b['returnedUnits'] = 0
        changed += 1
        if not quiet:
            print('  %-50s -> %-5s %s' % (str(b.get('bet'))[:50], s['result'],
                                          ('$%.2f back' % s['ret']) if s.get('ret') else ''))
    for n in notes:
        print('  ' + n)
    if changed and not dry_run:
        if isinstance(doc, dict):
            doc['bets'] = bets
            doc['updated'] = __import__('datetime').datetime.utcnow().isoformat(
                timespec='seconds') + 'Z'
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(doc, fh, separators=(',', ':'))
    print('%s%d settled, %d still running' % ('(dry run) ' if dry_run else '', changed, held))
    return changed, held


def selftest():
    fails = []

    def chk(name, cond, detail=''):
        print(('  ok   ' if cond else '  FAIL ') + name + ('  ' + detail if detail else ''))
        if not cond:
            fails.append(name)

    rows = [{'Player': 'A Man', 'MatchId': 'G1', 'Team': 'JAX', 'Opp': 'PHI', 'recYds': 49,
             'snapPct': 80, 'receptions': 0},
            {'Player': 'B Man', 'MatchId': 'G1', 'Team': 'PHI', 'Opp': 'JAX', 'recYds': 0,
             'snapPct': 0.05}]
    by_name, fields = index_logs(rows)
    data = lambda sport: (by_name, fields)
    leg = lambda **kw: dict({'sport': 'nfl', 'player': 'A Man', 'market': 'recYds',
                             'matchId': 'G1', 'side': 'over'}, **kw)
    bet = lambda legs, **kw: dict({'stake': 10, 'odds': 1.9, 'legs': legs}, **kw)

    chk('a clear over wins', settle(bet([leg(line=40)]), data)['result'] == 'win')
    chk('and pays stake x odds', settle(bet([leg(line=40)]), data)['ret'] == 19)
    chk('a clear over that misses loses', settle(bet([leg(line=60)]), data)['result'] == 'loss')
    chk('an under that lands wins',
        settle(bet([leg(line=60, side='under')]), data)['result'] == 'win')
    chk('exactly on the line is a push, refunded',
        settle(bet([leg(line=49)]), data)['result'] == 'void'
        and settle(bet([leg(line=49)]), data)['ret'] == 10)
    chk('a push inside a multi is held for a human',
        settle(bet([leg(line=40), leg(line=49)], odds=4), data)['result'] == 'review')
    chk('one leg down kills the multi',
        settle(bet([leg(line=40), leg(line=60)], odds=4), data)['result'] == 'loss')
    chk('an unknown player is held, never dropped',
        settle(bet([leg(player='Nobody')]), data)['legs'][0]['why'] == 'noplayer')
    chk('an unplayed game is held',
        settle(bet([leg(matchId='G9')]), data)['legs'][0]['why'] == 'notyet')
    chk('a leg with no game to point at is held and named',
        settle(bet([{'sport': 'nfl', 'player': 'A Man', 'market': 'recYds', 'line': 10}]),
               data)['legs'][0]['why'] == 'unpinned')
    r_un = settle(bet([leg(market='firstScorer', line=0.5)]), data)
    chk('a market the logs never carry is HELD, not refunded',
        r_un['result'] == 'pending' and r_un['legs'][0]['why'] == 'unverifiable',
        '(refunding it publishes a neutral result for a bet that had a real outcome)')
    chk('while a man who played and caught nothing is a real zero',
        settle(bet([leg(market='receptions', line=0.5)]), data)['result'] == 'loss')
    r_np = settle(bet([leg(player='B Man', market='receptions', line=0.5)]), data)
    chk('while a man who did not take the field is a void, which books refund',
        r_np['result'] == 'void' and r_np['legs'][0]['why'] == 'didnotplay',
        '(snapPct below the floor)')
    chk('a bet with no legs grades to nothing', settle(bet([]), data) is None)
    chk('a sport we do not carry is held',
        settle(bet([leg(sport='ncaaf')]), data)['legs'][0]['why'] == 'nodata')

    print('\nselftest: ' + ('FAILED - ' + ', '.join(fails) if fails else 'ALL PASS ok'))
    return 1 if fails else 0


def emit(root, path):
    """Every pending bet's full verdict as JSON on stdout. Writes nothing.

    This is what tests/grader_test.js reads to compare this grader against the page's engine,
    bet for bet and leg for leg. It reports the HELD ones too - "pending, because that name is
    not in the logs" is a verdict, and one the two have to agree on just as much as a win.
    """
    doc = load(path) or []
    bets = doc if isinstance(doc, list) else (doc.get('bets') or [])
    cache = {}

    def data(sport):
        if sport not in cache:
            cache[sport] = index_logs(gamelogs_for(root, sport))
        return cache[sport]

    out = {}
    for b in bets:
        if not isinstance(b, dict) or b.get('result') != 'pending':
            continue
        s = settle(b, data)
        out[b.get('id')] = None if s is None else {
            'result': s['result'], 'ret': s.get('ret'),
            'legs': [{'res': x['res'], 'why': x.get('why'), 'have': x.get('have')}
                     for x in s.get('legs', [])]}
    json.dump(out, sys.stdout)
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default='.', help='repo root (default .)')
    ap.add_argument('--file', default=None, help='the ledger (default <root>/data/results.json)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--emit', action='store_true',
                    help='print every pending bet\'s verdict as JSON and write nothing')
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    if a.emit:
        sys.exit(emit(a.root, a.file or os.path.join(a.root, 'data', 'results.json')))
    sys.exit(0 if grade(a.root, a.file or os.path.join(a.root, 'data', 'results.json'),
                        dry_run=a.dry_run)[0] >= 0 else 1)
