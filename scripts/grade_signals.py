#!/usr/bin/env python3
"""Grade the signal ledger and report how each tile has actually done.

    python scripts/grade_signals.py [--log data/signal_log.json] [--root .] [--stake 1]
    python scripts/grade_signals.py --regrade void      # re-settle rows a bug wrote off
    python scripts/grade_signals.py --selftest

Every row in the ledger was captured at a fixed time before its game (see snapshot_signals.js).
This settles the ones whose game has been played, by looking the player up in that sport's own
gamelogs, and then reports per TILE: bets, hit rate, units. That per-tile line is the number worth
publishing - "Green Lights: 118 bets, 61%, +4.2u" says more than any feature list, and it is also
how a tile that does not work gets found and retired.

A bet is settled only when the player has a row for that game. A player who did not take the field
is VOID, not a loss: the signal said what he would do if he played.

FINDING THE GAME IN THE LOGS
This used to be one line - match the gamelog's date against the bet's date - and it was wrong in
two ways that between them threw away most of the ledger.

  1. The bet's start is a UTC kick-off; the gamelogs carry the LOCAL game date. A 7:40pm Pacific
     puck drop is 2026-10-07T01:40Z in the fixture and 2026-10-06 in the logs. Every game starting
     after midnight UTC therefore found no row and was written off as "did not play" - 21 of the
     first 36 NHL rows, each of which had a real, gradeable performance sitting one day earlier.
  2. The NFL and AFL gamelogs carry no date field AT ALL, only Year/Week/MatchId. No date can ever
     match, so every NFL row in the ledger would have been voided however long we waited.

So the game is now identified in three descending orders of confidence: the fixture's MatchId where
the row carries one, then an exact date, then a date one day either side GATED on the opponent
matching the fixture - the gate is what stops a one-day slide from grading against the wrong game.

AND IT NO LONGER VOIDS WHAT IT CANNOT YET SEE
"No row for this player" means he did not play only if the logs have actually caught up to that
game. The MLB gamelogs stop at the end of the regular season, so all eight postseason rows were
voided for a feed that is never going to carry them; the same fuse was lit under 51 EPL rows whose
gameweek had not been built yet. A row is now only written off once the logs hold a game LATER than
this one - strictly later, so a half-built slate cannot void the games it has not reached. Anything
else stays pending and is counted as stalled, because a visible gap is worth more than a confident
wrong answer.
"""
import argparse
import datetime
import json
import math
import os
import re
import sys
import tempfile
from collections import defaultdict

# a sport missing here is never graded, so its signals sit in the ledger forever as 'pending'
SPORT_DIR = {'afl': 'AFL', 'nfl': 'nfl', 'epl': 'EPL', 'mlb': 'mlb', 'nbl': 'nbl', 'nhl': 'nhl',
             'nba': 'nba'}

# '2026_05_CHI_GB' - the season and week are in the id, which is the only ordering the NFL and AFL
# logs give us, since they carry no date
WEEK_ID = re.compile(r'^(\d{4})_(\d{1,2})_')


def load(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def gamelogs_for(root, sport):
    """Every gamelog row for a sport, whether it ships one file or one per season."""
    base = os.path.join(root, SPORT_DIR.get(sport, sport), 'data')
    rows = load(os.path.join(base, 'gamelogs.json'))
    if isinstance(rows, list) and rows:
        return rows
    meta = load(os.path.join(base, 'meta.json'), {}) or {}
    out = []
    for name in meta.get('gamelogFiles') or []:
        part = load(os.path.join(base, name))
        if isinstance(part, list):
            out.extend(part)
    return out


def norm(s):
    return ''.join(c for c in str(s or '').lower() if c.isalnum())


def log_day(row):
    return str(row.get('Date') or row.get('date') or '')[:10]


def stat_of(row, market):
    """The market's value on a gamelog row, trying the aliases each sport uses."""
    alias = {
        'anytimeTd': ('totalTds', 'anytimeTd'),
        'points': ('points', 'PTS'),
        'shots': ('shots', 'SOG'),
        'goals': ('goals', 'G'),
        'assists': ('assists', 'A'),
    }
    for k in alias.get(market, (market,)):
        if k in row and row[k] is not None:
            return row[k]
    if market == 'anytimeTd':
        rec, rush = row.get('recTds'), row.get('rushTds')
        if rec is not None or rush is not None:
            return (rec or 0) + (rush or 0)
    return None


STALE_DAYS = 2      # a game younger than this is never written off, whatever the logs look like


def sides_of(bet):
    """The two teams in the fixture the ledger recorded, as 'AWAY@HOME'."""
    parts = str(bet.get('fixture') or '').split('@')
    return {norm(p) for p in parts if norm(p)}


def week_of(mid):
    m = WEEK_ID.match(str(mid or ''))
    return (int(m.group(1)), int(m.group(2))) if m else None


class Coverage:
    """How far this sport's gamelogs have actually got.

    The question a grader has to answer before voiding anything is not "is this game old?" but
    "would a row for it be here by now?". Both answers below are deliberately STRICT: the logs
    must hold a game LATER than this one, so a slate that is half-built - NFL week 5 landed with
    one game of fourteen in it - cannot void the games it simply has not reached yet.
    """

    def __init__(self, logs):
        self.max_day = max((log_day(r) for r in logs if log_day(r)), default='')
        weeks = [week_of(r.get('MatchId')) for r in logs]
        wk = [w for w in weeks if w]
        # the logs' own ids do not carry the week for every sport, so fall back to Year/Week
        for r in logs:
            y, w = r.get('Year'), r.get('Week')
            if y and w:
                try:
                    y, w = int(y), int(w)
                except (TypeError, ValueError):
                    continue
                # a 'Week' is a round number, not a date. The MLB logs put 20260927 in that field,
                # and a comparison against it would be meaningless - though harmless today, since
                # a sport with dates never reaches the week path at all.
                if 1 <= w <= 30:
                    wk.append((y, w))
        self.max_week = max(wk) if wk else None

    def reached(self, bet):
        """True when the logs hold a game strictly later than this bet's, so a missing row means
        he did not play rather than the feed being behind."""
        day = str(bet.get('start') or '')[:10]
        if self.max_day and day:
            return self.max_day > day
        wk = week_of(bet.get('matchId'))
        if wk and self.max_week:
            return self.max_week > wk
        return False        # no way to tell - never guess, leave it pending and visible


def rows_for(bet, idx, by_match):
    """The player's gamelog rows for this bet's game, most confident route first.

    Returns (rows, how) so the caller can say WHICH route found it - a ledger whose matching rule
    is invisible is a ledger nobody can audit.
    """
    mine = idx.get(norm(bet.get('player')), [])
    if not mine:
        return [], 'noplayer'

    # 1. the fixture's own id. No date arithmetic, no ambiguity, and the only route that works for
    #    the NFL and AFL logs, which carry no date at all.
    mid = bet.get('matchId')
    if mid and by_match.get(str(mid)):
        hit = [r for r in mine if str(r.get('MatchId')) == str(mid)]
        if hit:
            return hit, 'matchid'

    day = str(bet.get('start') or '')[:10]
    if not day:
        return [], 'nodate'

    # 2. the date as recorded
    hit = [r for r in mine if log_day(r) == day]
    if hit:
        return hit, 'date'

    # 3. one day either side - a UTC kick-off after midnight logs under the previous local date -
    #    but ONLY against a row whose opponent is one of the two teams in this fixture. Without
    #    that gate a one-day slide happily grades a player against the wrong game.
    sides = sides_of(bet)
    if not sides:
        return [], 'nofixture'
    try:
        d = datetime.date.fromisoformat(day)
    except (TypeError, ValueError):
        return [], 'nodate'
    near = {(d - datetime.timedelta(days=1)).isoformat(), (d + datetime.timedelta(days=1)).isoformat()}
    hit = [r for r in mine
           if log_day(r) in near
           and (norm(r.get('Opp')) in sides or norm(r.get('Team')) in sides)]
    if hit:
        # both teams have to be the fixture's, where the row says who both were
        tight = [r for r in hit if not r.get('Opp') or not r.get('Team')
                 or {norm(r.get('Opp')), norm(r.get('Team'))} <= sides]
        if tight:
            return tight, 'offset'
    return [], 'nogame'


def settle(bet, idx, by_match, cover):
    """win / loss / void / None for one logged bet, plus how the game was found.

    None means "still open" - the game is to come, or the logs have not reached it yet.
    """
    played, how = rows_for(bet, idx, by_match)
    if not played:
        day = str(bet.get('start') or '')[:10]
        try:
            age = (datetime.date.today() - datetime.date.fromisoformat(day)).days
        except (TypeError, ValueError):
            return None, None, 'nodate'
        if age <= STALE_DAYS:
            return None, None, how          # too soon to call anything
        if not cover.reached(bet):
            return None, None, 'behind'     # the feed is behind, not the player absent
        return 'void', None, how            # logs have moved past this game: he did not play
    v = None
    for r in played:
        got = stat_of(r, bet.get('market'))
        if got is not None:
            v = (v or 0) + float(got)
    if v is None:
        return 'void', None, 'nostat'       # he played, but the feed does not carry that stat
    line = bet.get('line')
    if line is None:
        return None, v, how
    if bet.get('side') == 'under':
        return ('win' if v < line else 'loss'), v, how
    return ('win' if v > line else 'loss'), v, how


def grade(rows, root, regrade='none'):
    """Settle every open row in place. Returns (settled, how_counts, waiting_by_sport)."""
    if regrade == 'void':
        # a void is the one verdict a bug can fabricate out of silence, so it is the one that is
        # safe to revisit. A win or a loss is history and is never touched.
        for b in rows:
            if b.get('result') == 'void':
                b['result'], b['actual'] = None, None

    by_sport = defaultdict(list)
    for b in rows:
        if b.get('result') is None:
            by_sport[b.get('sport')].append(b)

    settled = 0
    hows = defaultdict(int)
    waiting = defaultdict(lambda: {'open': 0, 'stalled': 0})
    for sport, bets in by_sport.items():
        logs = gamelogs_for(root, sport)
        if not logs:
            waiting[sport]['open'] += len(bets)
            waiting[sport]['stalled'] += len(bets)
            hows['nologs'] += len(bets)
            continue
        idx = defaultdict(list)
        by_match = defaultdict(list)
        for r in logs:
            idx[norm(r.get('Player') or r.get('player') or r.get('name'))].append(r)
            if r.get('MatchId'):
                by_match[str(r['MatchId'])].append(r)
        cover = Coverage(logs)
        for b in bets:
            res, actual, how = settle(b, idx, by_match, cover)
            hows[(res or 'open') + ':' + how] += 1
            if res:
                b['result'], b['actual'] = res, actual
                settled += 1
            else:
                waiting[sport]['open'] += 1
                if how == 'behind':
                    waiting[sport]['stalled'] += 1
    return settled, dict(hows), {k: v for k, v in waiting.items()}


def price_of(b):
    """The row's price, or None when it does not have one worth counting.

    Decimal odds are always greater than 1. Anything else - a missing price, a zero, a 1.00 that
    pays nothing back - is not a price, and must not be quietly read as even money: `price or 1`
    used to turn a priceless winner into a bet that returned exactly its stake.
    """
    try:
        p = float(b.get('price'))
    except (TypeError, ValueError):
        return None
    return p if p > 1 else None


def report_of(rows, stake):
    """Per tile: bets, record, and units over the rows that HAVE a price.

    'p' counts the decided rows that carry one. Units and ROI are measured over those alone, so a
    row whose price was lost still counts toward the hit rate - the signal either came in or it
    did not - without inventing a return for it.
    """
    tiles = defaultdict(lambda: {'n': 0, 'w': 0, 'l': 0, 'v': 0, 'u': 0.0, 'p': 0})
    for b in rows:
        if not b.get('result'):
            continue
        t = tiles[(b.get('sport'), b.get('tile'))]
        t['n'] += 1
        price = price_of(b)
        if b['result'] == 'win':
            t['w'] += 1
            if price is not None:
                t['u'] += (price - 1) * stake
                t['p'] += 1
        elif b['result'] == 'loss':
            t['l'] += 1
            if price is not None:
                t['u'] -= stake
                t['p'] += 1
        else:
            t['v'] += 1
    return sorted(tiles.items(), key=lambda kv: -kv[1]['u'])


# The three tiles that rendered the line in front of the price, so every price the snapshot read
# off them was the line. The price at the moment of capture is gone - it is not in the ledger and
# re-reading it from a later odds file would be a different number wearing the same label - so the
# repair is to admit it is missing, not to replace it.
LINE_FIRST_TILES = {'elite matchups', 'bunnies', 'bogey'}


def repair_prices(rows):
    """Null the prices that are provably the line. Returns how many were cleared."""
    n = 0
    for b in rows:
        if str(b.get('tile') or '').strip().lower() not in LINE_FIRST_TILES:
            continue
        price, line = price_of(b), b.get('line')
        if price is None or line is None:
            continue
        try:
            line = float(line)
        except (TypeError, ValueError):
            continue
        # the box printed ceil(line) for an over - line+1 where the line is already whole - and the
        # bare line for an under. Both conditions together with the tile, so a genuine $3.00 on a
        # 2.5 line in any OTHER tile is never touched.
        ask = line + 1 if line == int(line) else math.ceil(line)
        if abs(price - ask) < 1e-9 or abs(price - line) < 1e-9:
            b['price'] = None
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--log', default='data/signal_log.json')
    ap.add_argument('--root', default='.')
    ap.add_argument('--stake', type=float, default=1.0)
    ap.add_argument('--out', default=None)
    ap.add_argument('--regrade', choices=['none', 'void'], default='none',
                    help="'void' clears rows previously written off and settles them again; wins "
                         "and losses are never revisited")
    ap.add_argument('--repair-prices', action='store_true',
                    help='clear the prices on Elite Matchups / Bunnies / Bogey rows that are '
                         'actually the line (see LINE_FIRST_TILES)')
    ap.add_argument('--dry-run', action='store_true', help='grade and report, write nothing')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    log = load(a.log, {'rows': []}) or {'rows': []}
    rows = log.get('rows') or []
    if not rows:
        print('signal ledger is empty - nothing to grade')
        return 0

    fixed = repair_prices(rows) if a.repair_prices else 0
    if a.repair_prices:
        print('repaired %d row(s) whose price was the line' % fixed)
    settled, hows, waiting = grade(rows, a.root, a.regrade)
    order = report_of(rows, a.stake)

    print('%-6s %-22s %5s %6s %8s %8s' % ('sport', 'tile', 'bets', 'hit', 'units', 'roi'))
    for (sport, tile), t in order:
        decided = t['w'] + t['l']
        hit = ('%.0f%%' % (t['w'] / decided * 100)) if decided else '-'
        # staked over the PRICED rows only - the ones the units were actually measured on
        staked = t['p'] * a.stake
        roi = ('%+.1f%%' % (t['u'] / staked * 100)) if staked else '-'
        note = '' if t['p'] == decided else '  (%d of %d priced)' % (t['p'], decided)
        print('%-6s %-22s %5d %6s %+8.2f %8s%s'
              % (sport, (tile or '?')[:22], t['n'], hit, t['u'], roi, note))

    tiles = [t for _, t in order]
    tot_u = sum(t['u'] for t in tiles)
    tot_d = sum(t['w'] + t['l'] for t in tiles)
    print('-' * 60)
    print('%-29s %5d %6s %+8.2f' % ('all tiles', sum(t['n'] for t in tiles),
          ('%.0f%%' % (sum(t['w'] for t in tiles) / tot_d * 100)) if tot_d else '-', tot_u))
    print('settled this run: %d | still open: %d'
          % (settled, sum(1 for b in rows if not b.get('result'))))

    # WHERE THE ROWS WENT. A ledger that silently drops most of what it captures looks exactly like
    # a ledger with nothing to say, which is how the UTC bug survived three weeks.
    if waiting:
        for sport in sorted(waiting):
            w = waiting[sport]
            note = ' (%d waiting on the gamelog feed)' % w['stalled'] if w['stalled'] else ''
            print('  %-5s %d open%s' % (sport, w['open'], note))
    if hows:
        print('  matched: ' + ', '.join('%s=%d' % (k, v) for k, v in sorted(hows.items())))

    if a.dry_run:
        print('(dry run: nothing written)')
        return 0

    with open(a.log, 'w') as fh:
        json.dump(log, fh)
    if a.out:
        report = [{'sport': s, 'tile': t, **v} for (s, t), v in order]
        os.makedirs(os.path.dirname(a.out) or '.', exist_ok=True)
        with open(a.out, 'w') as fh:
            json.dump({'updated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
                       'tiles': report,
                       'waiting': {k: v for k, v in sorted(waiting.items())}}, fh)
        print('wrote ' + a.out)
    return 0


# --------------------------------------------------------------------------------------------
# selftest - every case below is one the live ledger got wrong, or one a fix could newly break
# --------------------------------------------------------------------------------------------
def selftest():
    fails = []

    def ok(name, cond, note=''):
        print(('  ok   ' if cond else '  FAIL ') + name + (('  ' + note) if note else ''))
        if not cond:
            fails.append(name)

    root = tempfile.mkdtemp(prefix='sigtest')

    def write_logs(sport, logs, meta=None):
        base = os.path.join(root, SPORT_DIR[sport], 'data')
        os.makedirs(base, exist_ok=True)
        with open(os.path.join(base, 'gamelogs.json'), 'w') as fh:
            json.dump(logs, fh)
        with open(os.path.join(base, 'meta.json'), 'w') as fh:
            json.dump(meta or {}, fh)

    def bet(**kw):
        b = {'sport': 'nhl', 'player': 'Jack Eichel', 'market': 'shots', 'line': 2.5,
             'side': 'over', 'fixture': 'VGK@SEA', 'start': '2026-10-07T01:40Z',
             'tile': 'Streakers', 'price': 1.9, 'result': None, 'actual': None}
        b.update(kw)
        return b

    def nhl_row(**kw):
        r = {'Player': 'Jack Eichel', 'Team': 'VGK', 'Opp': 'SEA', 'Date': '2026-10-06',
             'MatchId': 'nhl1', 'shots': 4}
        r.update(kw)
        return r

    old = '2020-01-02T01:40Z'      # long past, so the stale floor never holds a case open

    print('THE BUG THAT VOIDED EVERY NIGHT GAME')
    # a UTC kick-off after midnight logs under the previous local date
    write_logs('nhl', [nhl_row(Date='2020-01-01'), nhl_row(Player='Someone Else', Date='2020-01-05')])
    rows = [bet(start=old)]
    grade(rows, root)
    ok('a game starting after midnight UTC grades off the previous local date',
       rows[0]['result'] == 'win' and rows[0]['actual'] == 4,
       '(was: void)')

    # ...but only against the right game
    write_logs('nhl', [nhl_row(Date='2020-01-01', Opp='COL', Team='DAL'),
                       nhl_row(Player='Someone Else', Date='2020-01-05')])
    rows = [bet(start=old)]
    grade(rows, root)
    # he has a row a day away, but against neither of this fixture's teams - so it is not his
    # performance in THIS game. Void is the honest answer; grading it would be a fabricated result.
    ok('a day either side is never graded against the wrong opponent',
       rows[0]['result'] == 'void' and rows[0]['actual'] is None,
       '(a 4-shot row for DAL v COL must not settle a VGK@SEA bet)')

    # an exact date still wins outright, and is not blended with a neighbouring day
    write_logs('nhl', [nhl_row(Date='2020-01-02', shots=1), nhl_row(Date='2020-01-01', shots=9),
                       nhl_row(Player='Someone Else', Date='2020-01-05')])
    rows = [bet(start=old)]
    grade(rows, root)
    ok('an exact date match is preferred over the day either side',
       rows[0]['result'] == 'loss' and rows[0]['actual'] == 1, '(1 shot, not 9)')

    print('THE LOGS THAT NEVER CARRIED A DATE AT ALL')
    # NFL and AFL gamelogs have Year/Week/MatchId and no date, so no date could ever match
    write_logs('nfl', [{'Player': 'Puka Nacua', 'Team': 'LA', 'Opp': 'BUF', 'Year': '2026',
                        'Week': 5, 'MatchId': '2026_05_BUF_LA', 'recYds': 88},
                       {'Player': 'Someone Else', 'Year': '2026', 'Week': 6,
                        'MatchId': '2026_06_LA_SF', 'recYds': 10}])
    rows = [bet(sport='nfl', player='Puka Nacua', market='recYds', line=70.5,
                fixture='BUF@LA', matchId='2026_05_BUF_LA', start=old)]
    grade(rows, root)
    ok('a sport whose logs carry no date grades off the fixture MatchId',
       rows[0]['result'] == 'win' and rows[0]['actual'] == 88, '(was: void, always)')

    rows = [bet(sport='nfl', player='Puka Nacua', market='recYds', line=70.5,
                fixture='BUF@LA', start=old)]
    grade(rows, root)
    ok('and without a MatchId it stays open rather than being written off',
       rows[0]['result'] is None)

    print('WHAT IT WILL NOT VOID')
    # the MLB postseason: the logs stop at the regular season and are never going to carry it
    write_logs('mlb', [{'Player': 'Shohei Ohtani', 'Team': 'LAD', 'Opp': 'SF',
                        'Date': '2019-09-27', 'MatchId': 'm1', 'HR': 1}])
    rows = [bet(sport='mlb', player='Shohei Ohtani', market='HR', line=0.5,
                fixture='LAD@ATL', start='2019-10-06T22:00Z')]
    _, _, waiting = grade(rows, root)
    ok('a game past the end of the feed is left open, not voided',
       rows[0]['result'] is None and waiting.get('mlb', {}).get('stalled') == 1,
       '(8 real MLB rows were voided this way)')

    # a half-built slate must not void the games it has not reached
    write_logs('nfl', [{'Player': 'Someone Else', 'Team': 'TB', 'Opp': 'DAL', 'Year': '2026',
                        'Week': 5, 'MatchId': '2026_05_TB_DAL', 'recYds': 10}])
    rows = [bet(sport='nfl', player='Puka Nacua', market='recYds', line=70.5,
                fixture='BUF@LA', matchId='2026_05_BUF_LA', start=old)]
    grade(rows, root)
    ok('one game of a week landing does not void the rest of that week',
       rows[0]['result'] is None, '(week 5 arrived with 1 game of 14)')

    # once the feed is past it, an absent player IS a void
    write_logs('nfl', [{'Player': 'Someone Else', 'Team': 'LA', 'Opp': 'SF', 'Year': '2026',
                        'Week': 6, 'MatchId': '2026_06_LA_SF', 'recYds': 10}])
    rows = [bet(sport='nfl', player='Puka Nacua', market='recYds', line=70.5,
                fixture='BUF@LA', matchId='2026_05_BUF_LA', start=old)]
    grade(rows, root)
    ok('a player absent from a week the feed HAS reached is void',
       rows[0]['result'] == 'void')

    # and a game that has only just finished is never called either way
    write_logs('nhl', [nhl_row(Date='2020-01-01')])
    today = datetime.date.today().isoformat()
    rows = [bet(start=today + 'T01:40Z', player='Nobody At All')]
    grade(rows, root)
    ok('a game from today is never written off', rows[0]['result'] is None)

    print('REGRADING, AND WHAT IT MUST NOT TOUCH')
    write_logs('nhl', [nhl_row(Date='2020-01-01'), nhl_row(Player='Someone Else', Date='2020-01-05')])
    rows = [bet(start=old, result='void'),
            bet(start=old, result='loss', actual=0, line=2.5),
            bet(start=old, result='win', actual=9, line=2.5)]
    settled, _, _ = grade(rows, root, regrade='void')
    ok('--regrade void re-settles a row a bug wrote off',
       rows[0]['result'] == 'win' and settled == 1)
    ok('and leaves a recorded loss alone',
       rows[1]['result'] == 'loss' and rows[1]['actual'] == 0,
       '(it would grade as a win now; history is not rewritten)')
    ok('and leaves a recorded win alone',
       rows[2]['result'] == 'win' and rows[2]['actual'] == 9)

    rows = [bet(start=old, result='void')]
    grade(rows, root, regrade='none')
    ok('without the flag a void is left exactly as it was', rows[0]['result'] == 'void')

    print('THE ARITHMETIC BEHIND THE PUBLISHED LINE')
    rows = [bet(result='win', price=2.5, tile='T'), bet(result='loss', price=2.5, tile='T'),
            bet(result='void', price=2.5, tile='T')]
    (_, t), = report_of(rows, 1.0)
    ok('a void is not a loss in the units', abs(t['u'] - 0.5) < 1e-9, '(+1.5 -1.0, void ignored)')
    ok('and is not counted in the hit rate', t['w'] + t['l'] == 2 and t['v'] == 1)

    print('THE PRICE THAT WAS NOT A PRICE')
    # Elite Matchups renders "270+ $3.05" - the line, then the price - and the scrape took the
    # first number it saw. A 269.5-yard passing line came back as a price of 270, and one winner
    # paid 269 units. The published table read +305.0u at +10167% ROI.
    rows = [bet(result='win', tile='Elite Matchups', market='passYds', line=269.5, price=270),
            bet(result='win', tile='Elite Matchups', market='passAtt', line=37.5, price=38),
            bet(result='loss', tile='Elite Matchups', market='recYds', line=59.5, price=60)]
    n = repair_prices(rows)
    ok('a price that is only the line rounded up is cleared', n == 3
       and all(b['price'] is None for b in rows), '(270 on a 269.5 line is not odds)')
    (_, t), = report_of(rows, 1.0)
    ok('and the tile then claims no units at all', t['u'] == 0 and t['p'] == 0,
       '(was +305.0u from three bets)')
    ok('but the signals still count toward the record',
       t['n'] == 3 and t['w'] == 2 and t['l'] == 1,
       '(the bets happened; only what they paid is unknown)')

    # a priceless winner must not be read as even money
    rows = [bet(result='win', tile='T', price=None), bet(result='loss', tile='T', price=None)]
    (_, t), = report_of(rows, 1.0)
    ok('a row with no price is kept out of the units entirely',
       t['u'] == 0 and t['p'] == 0 and t['w'] + t['l'] == 2,
       '(a missing price used to be read as 1.00 - a winner that returned the stake)')
    rows = [bet(result='win', tile='T', price=1.0)]
    (_, t), = report_of(rows, 1.0)
    ok('and so is a price of 1.00, which pays nothing', t['p'] == 0)

    # the repair is scoped: an identical-looking price in another tile is left alone
    rows = [bet(result='win', tile='Shot Machines', market='shots', line=2.5, price=3.0)]
    ok('a genuine $3.00 on a 2.5 line in another tile is untouched',
       repair_prices(rows) == 0 and rows[0]['price'] == 3.0,
       '(only the three tiles that printed the line first)')
    rows = [bet(result='win', tile='Bunnies', market='shots', line=2.5, price=3.4)]
    ok('and a real price in an affected tile survives too',
       repair_prices(rows) == 0 and rows[0]['price'] == 3.4,
       '(3.4 is not ceil(2.5))')
    rows = [bet(result='win', tile='Bogey', market='recYds', line=59.5, price=59.5, side='under')]
    ok('an under row that logged the bare line is cleared as well',
       repair_prices(rows) == 1 and rows[0]['price'] is None, '("U59.5 $2.10")')

    print(('\nFAILED: ' + ', '.join(fails)) if fails else '\nall good')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main() or 0)
