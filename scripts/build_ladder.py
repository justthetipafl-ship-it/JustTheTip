#!/usr/bin/env python3
"""JTT shared ladder challenge -> writes <base>/ladder.json for one sport.
Usage: build_ladder.py <data_dir> [bookmaker]

Preferred mode (odds present): each day's pick is the best ~$2 same-game multi
(2-4 legs, any stat) from a single bookmaker, chosen by highest combined hit-rate.
Fallback mode (no odds, e.g. AFLW): a single-stat banker on [fallback_stat].

Each run grades the previous pending pick (all legs must clear), moves the bankroll,
then adds the next pick. Keeps a rolling 10 days. Handles split game-log files."""
import json
import math
import os
import re
import sys
import glob
import datetime
from itertools import combinations, product

START = 10.0
TARGET = 10000.0
MAX_RUNGS = 12
LADDER_BOOK = 'sportsbet'   # the ladder is placed at one set book
MIN_GAMES = 6
ODDS_LO = 1.85          # target SGM price window (~$2)
ODDS_HI = 2.20
LEG_LO = 1.14           # per-leg price floor/ceiling worth combining
LEG_HI = 1.75
HIT_WINDOW = 12         # recent games used for hit-rate
TOP_PER_GAME = 10       # legs per game/book fed to the combo search

# odds market -> game-log field (only markets we can grade)
MKT = {'disposals': 'disposals', 'goals': 'goals', 'marks': 'marks', 'tackles': 'tackles',
       'dreamteam': 'dreamteam', 'kicks': 'kicks', 'handballs': 'handballs',
       'clearances': 'clearances', 'hitouts': 'hitouts', 'fantasy': 'dreamteam',
       'points': 'points', 'rebounds': 'rebounds', 'assists': 'assists', 'threes': 'threes',
       'shots': 'shots', 'saves': 'saves', 'passYds': 'passYds', 'rushYds': 'rushYds',
       'recYds': 'recYds', 'receptions': 'receptions', 'H': 'H', 'TB': 'TB', 'HR': 'HR',
       'RBI': 'RBI', 'R': 'R', 'SB': 'SB', 'BB': 'BB', 'K': 'SO'}


def load(path):
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    return None


def load_gamelogs(base):
    rows = []
    for fp in sorted(glob.glob(os.path.join(base, 'gamelogs*.json'))):
        try:
            with open(fp) as fh:
                d = json.load(fh)
            if isinstance(d, list):
                rows.extend(d)
        except Exception:
            pass
    return rows


def rnum(name):
    m = re.search(r'(\d+)', str(name or ''))
    return int(m.group(1)) if m else 0


def build_index(gl):
    byp = {}
    for r in gl:
        nm = r.get('Player')
        if not nm:
            continue
        rec = byp.setdefault(nm, {'team': r.get('Team'), 'games': []})
        rec['team'] = r.get('Team')
        rec['games'].append(r)
    for nm in byp:
        byp[nm]['games'].sort(key=lambda r: (int(r.get('Year', 0) or 0), rnum(r.get('RoundName') or r.get('Week'))))
        seen, ded = set(), []
        for r in byp[nm]['games']:                                    # stable sort keeps the fresh row first
            k = (int(r.get('Year', 0) or 0), rnum(r.get('RoundName') or r.get('Week')))
            if k in seen:
                continue
            seen.add(k); ded.append(r)
        byp[nm]['games'] = ded
    return byp


def _wilson_lower(hits, n, z=1.28):
    """~80% one-sided lower confidence bound on the hit rate. Rewards sample size:
    a 25/30 outranks a 4/4, so the ladder stops favouring thin-record players."""
    if n <= 0:
        return 0.0
    p = hits / float(n)
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = p + z2 / (2.0 * n)
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def hit_rate(rec, field, line):
    vals = [r.get(field) for r in rec['games'][-HIT_WINDOW:] if r.get(field) is not None]
    vals = [float(v) for v in vals]
    n = len(vals)
    if n < 4:
        return None
    hits = sum(1 for v in vals if v >= line)
    return _wilson_lower(hits, n)


ROLE_FIELD = {'AFL': 'tog', 'nfl': 'snapPct', 'nbl': 'minutes', 'nhl': 'toiMin', 'EPL': 'min'}
ROLE_FLOOR = {'AFL': 75, 'nfl': 55, 'nbl': 18, 'nhl': 12, 'EPL': 60}


def role_ok(rec, base):
    """Is he a regular, or a fringe player who might not take the field at all?

    Measured across every gamelog: a player who featured in 5 of his team's last 6 games still only
    features in the NEXT one 72.7% of the time under 45% snaps (NFL) or 68.6% under 70% TOG (AFL),
    against 86-92% for the regulars. A hit rate cannot see that coming - his record only contains
    games he played - and a leg that never starts kills the day's ladder.
    """
    sport = str(base).replace('\\', '/').strip('/').split('/')[-2] if '/' in str(base) else str(base)
    field = ROLE_FIELD.get(sport)
    if not field:
        return True                                    # MLB: no minutes to read
    vals = [float(g[field]) for g in rec['games'][-6:] if g.get(field) not in (None, '')]
    if not vals:
        return True                                    # nothing to judge on: leave him alone
    return (sum(vals) / len(vals)) >= ROLE_FLOOR.get(sport, 0)


def _mkleg(l, byp, base=''):
    nm = l.get('player'); mk = l.get('market'); ov = l.get('over')
    if nm is None or ov is None or mk not in MKT or ov < LEG_LO or ov > LEG_HI:
        return None
    rec = byp.get(nm)
    if not rec or len(rec['games']) < MIN_GAMES:
        return None
    if not role_ok(rec, base):
        return None
    hr = hit_rate(rec, MKT[mk], l.get('line'))
    if hr is None or hr < 0.5:
        return None
    return {'name': nm, 'market': mk, 'line': l.get('line'), 'over': ov, 'hr': hr}


def _combo_same_game(legs):
    """Best 2-4 distinct-player legs from one game, priced ~$2 (SGM)."""
    byname = {}
    for lg in legs:
        if lg['name'] not in byname or lg['hr'] > byname[lg['name']]['hr']:
            byname[lg['name']] = lg
    cand = sorted(byname.values(), key=lambda x: -x['hr'])[:TOP_PER_GAME]
    best = None
    n = len(cand)
    for size in (2, 3, 4):
        if n < size:
            break
        for combo in combinations(range(n), size):
            price = 1.0; hrp = 1.0
            for i in combo:
                price *= cand[i]['over']; hrp *= cand[i]['hr']
            if ODDS_LO <= price <= ODDS_HI and (best is None or hrp > best['hrp']):
                best = {'legs': [cand[i] for i in combo], 'hrp': hrp}
    return best


def _combo_cross_game(by_game):
    """Best 2-4 legs, ONE per game, priced ~$2 (cross-game multi -> honest independent pricing)."""
    games = {}
    for gi, legs in by_game.items():
        byline = {}
        for lg in legs:
            if lg['line'] not in byline or lg['hr'] > byline[lg['line']]['hr']:
                byline[lg['line']] = lg
        vals = list(byline.values())
        safe = sorted(vals, key=lambda x: -x['hr'])[:4]                       # safest bankers
        longer = sorted([l for l in vals if l['hr'] >= 0.55], key=lambda x: -x['over'])[:4]   # + longer legs so a combo can reach the ~$2 window even with few games
        seen, pool = set(), []
        for l in safe + longer:
            k = (l['name'], l['line'])
            if k in seen: continue
            seen.add(k); pool.append(l)
        games[gi] = pool
    gis = sorted(games.keys(), key=lambda gi: -games[gi][0]['hr'])[:6]
    best = None
    for size in (2, 3, 4):
        if len(gis) < size:
            break
        for gset in combinations(gis, size):
            pools = [games[gi] for gi in gset]
            for pick in product(*pools):
                price = 1.0; hrp = 1.0
                for lg in pick:
                    price *= lg['over']; hrp *= lg['hr']
                if ODDS_LO <= price <= ODDS_HI and (best is None or hrp > best['hrp']):
                    best = {'legs': list(pick), 'hrp': hrp}
    return best


def best_pick(base, gl, byp, book=LADDER_BOOK, fixtures=None, pool_only=False):
    """Cross-game multi across the slate (one book); SGM only when a single game is on."""
    od = load(os.path.join(base, 'odds.json'))
    fx = load(os.path.join(base, 'fixture.json')) or []
    if not od or not od.get('lines'):
        return None
    legs_all = list(od.get('lines') or []) + list(od.get('alt') or [])

    pteam = {nm: rec['team'] for nm, rec in byp.items()}
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    aest = datetime.timedelta(hours=10)                 # Brisbane, UTC+10, no DST
    today = (now + aest).date()

    def _start(g):
        for k in ('utc', 'gameTimeUTC', 'commence_time', 'commence', 'start'):
            v = g.get(k)
            if v:
                try:
                    return datetime.datetime.fromisoformat(
                        str(v).replace('Z', '').replace('+00:00', ''))
                except Exception:
                    pass
        return None

    def _today_upcoming(g):
        """A game on today's AEST date that hasn't started yet."""
        st = _start(g)
        if st is None:                                  # no start time -> use the date field
            d = g.get('date')
            return bool(d) and str(d) == today.isoformat()
        if st <= now:                                   # already started
            return False
        return (st + aest).date() == today              # same AEST calendar day

    def _gdate(g):
        st = _start(g)
        if st is not None:
            return (st + aest).date().isoformat()
        return str(g.get('date') or '')[:10]

    def _future(g):
        st = _start(g)
        if st is not None:
            return st > now                              # hasn't started yet
        return bool(g.get('date')) and _gdate(g) >= today.isoformat()

    playable = [g for g in fx if g.get('home') and g.get('away') and _future(g)]
    todays = [g for g in playable if _gdate(g) == today.isoformat()]
    if todays:                                           # prefer today's slate
        slate = todays
    elif playable:
        # The soonest upcoming slate, but only within MAX_AHEAD days. Unbounded, a Monday run
        # reached a game played the following Thursday: the bet then sat four days undecided, the
        # ladder could not compound, and the staleness clock - which runs from the day the rung was
        # struck - wrote the day off before kick-off. A ladder that climbs daily has to bet on
        # games that are about to be played, and skip the day when there are none.
        soonest = min(_gdate(g) for g in playable if _gdate(g))
        try:
            ahead = (datetime.date.fromisoformat(soonest) - today).days
        except (TypeError, ValueError):
            ahead = 0
        if ahead > MAX_AHEAD:
            print('  ladder: nothing playable within %d day(s) - next slate is %s (%d days away), '
                  'no rung today' % (MAX_AHEAD, soonest, ahead))
            return None
        slate = [g for g in playable if _gdate(g) == soonest]
    else:
        return None
    games = [(g.get('home'), g.get('away')) for g in slate]
    if not games:
        print('    %s: slate has no games' % base)
        return None
    team_game = {}
    for i, (h, a) in enumerate(games):
        team_game[h] = i; team_game[a] = i
    single = len(games) == 1

    # priced, gradeable, short legs grouped by (book, game)
    by_book = {}
    for l in legs_all:
        if (l.get('book') or '').lower() != book.lower():
            continue                               # ladder is locked to one bookmaker
        gi = team_game.get(pteam.get(l.get('player')))
        if gi is None:
            continue
        lg = _mkleg(l, byp, base)
        if lg is None:
            continue
        tm = pteam.get(lg['name']); h, a = games[gi]
        lg['team'] = tm
        lg['opp'] = a if tm == h else h
        by_book.setdefault(l.get('book'), {}).setdefault(gi, []).append(lg)

    if pool_only:
        # The raw candidates, keyed by book - a cross-sport multi still has to be placeable at ONE
        # book. year/round come from the slate's own fixture here, since the usual derivation reads
        # them off the chosen legs and nothing has been chosen yet.
        out = {}
        for bk, by_game in by_book.items():
            flat = []
            for gi, lgs in by_game.items():
                for lg in lgs:
                    lg = dict(lg); lg['game'] = gi
                    flat.append(lg)
            if flat:
                out[bk] = flat
        pyr, prd = 0, 0
        for fxg in (fixtures or []):
            if fxg.get('week') is None and fxg.get('round') is None:
                continue
            try:
                pyr = int(fxg.get('season') or fxg.get('year') or 0)
                prd = rnum(fxg.get('week') if fxg.get('week') is not None else fxg.get('round'))
            except (TypeError, ValueError):
                continue
            break
        return {'pool': out, 'game_date': (_gdate(slate[0]) if slate else None),
                'year': pyr, 'round': prd}
    best = None
    for book, by_game in by_book.items():
        if single:
            gi = next(iter(by_game))
            c = _combo_same_game(by_game[gi]); typ = 'sgm'
        else:
            if len(by_game) < 2:            # need >=2 games for a cross-game multi
                continue
            c = _combo_cross_game(by_game); typ = 'multi'
        if c and (best is None or c['hrp'] > best['hrp']):
            price = 1.0
            for lg in c['legs']:
                price *= lg['over']
            best = {'legs': c['legs'], 'hrp': c['hrp'], 'price': price, 'book': book, 'type': typ}

    if not best:
        print('    %s: %d game(s) on the slate but no build cleared the filters '
              '(no priced legs at %s, or not enough history)' % (base, len(games), book))
        return None
    picked = set(l['name'] for l in best['legs'])
    subleg = None
    for gi2, legs2 in by_book.get(best['book'], {}).items():
        for l2 in legs2:
            if l2['name'] in picked:
                continue
            if subleg is None or l2['hr'] > subleg['hr']:
                subleg = l2
    latest = (0, 0)
    for lg in best['legs']:
        g = byp[lg['name']]['games'][-1]
        latest = max(latest, (int(g.get('Year', 0) or 0), rnum(g.get('RoundName') or g.get('Week'))))
    target = (latest[0], latest[1] + 1)
    # the fixture knows which round is actually being played; the gamelogs only know what has been
    # published, and they lag. Prefer the fixture for the leg's own team where it carries a round.
    teams = {lg.get('team') for lg in best['legs'] if lg.get('team')}
    for fxg in (fixtures or []):
        if fxg.get('week') is None and fxg.get('round') is None:
            continue
        if teams and not (teams & {fxg.get('home'), fxg.get('away')}):
            continue
        yr = int(fxg.get('season') or fxg.get('year') or latest[0] or 0)
        rd = rnum(fxg.get('week') if fxg.get('week') is not None else fxg.get('round'))
        if (yr, rd) >= (latest[0], latest[1]):
            target = (yr, rd)
            break
    # carry the date of the game the rung was actually struck on: the staleness clock should run
    # from when the bet can be decided, not from when it was placed
    _gd = None
    try:
        _gd = _gdate(slate[0])
    except (IndexError, TypeError, NameError):
        _gd = None
    return {'legs': best['legs'], 'price': round(best['price'], 2), 'book': best['book'],
            # `round` is the last COMPLETED round at pick time; grading takes the next game after it
            'type': best['type'], 'sub': subleg, 'year': target[0], 'round': target[1],
            'game_date': _gd}



DNP = 'dnp'
MAX_AHEAD = 1         # a rung may be struck for today or tomorrow, never further out
STALE_DAYS = 3        # a pending day older than this is written off rather than freezing the ladder          # he never took the field in the rounds after the bet


def grade_leg(byp, name, field, line, year, rnd, stale_by=0, after_date=None):
    """Grade the leg against the game it was actually struck on.

    Two anchors, because the sports carry different keys:
      * MLB / NBL / NHL / EPL gamelogs have dates -> the first game AFTER the day the bet was made.
      * NFL / AFL have only year+round -> the row for THAT round exactly. Not ">= round": with a
        week-3 target and no week-3 row, that silently graded the week-4 game instead.
    Unresolved is not the same as lost. While the round has not been played (or the logs lag, which
    is why NFL days sat pending with week 2 half-loaded) it stays pending. Once the league is past
    it and he still has no row, he did not take the field and the day is void.
    """
    rec = byp.get(name)
    if not rec:
        return None
    if after_date:
        for r in rec['games']:
            d = str(r.get('Date') or r.get('date') or '')
            if d and d > after_date and r.get(field) is not None:
                return float(r.get(field)) >= line
        return DNP if stale_by > 0 else None
    # exact round first
    for r in rec['games']:
        y = int(r.get('Year', 0) or 0)
        rd = rnum(r.get('RoundName') or r.get('Week'))
        if (y, rd) == (year, rnd) and r.get(field) is not None:
            return float(r.get(field)) >= line
    # AFL finals are named, not numbered ("Preliminary Final" gives no usable round), so allow the
    # very next round before giving up. Anything further away is a different game, not this bet.
    best = None
    for r in rec['games']:
        y = int(r.get('Year', 0) or 0)
        rd = rnum(r.get('RoundName') or r.get('Week'))
        if y == year and 0 < rd - rnd <= 1 and r.get(field) is not None:
            if best is None or rd < best[0]:
                best = (rd, float(r.get(field)) >= line)
    if best:
        return best[1]
    return DNP if stale_by > 0 else None

def league_round(gl):
    """The latest (year, round) anywhere in the gamelogs - how far the league has actually got."""
    latest = (0, 0)
    for r in gl:
        latest = max(latest, (int(r.get('Year', 0) or 0), rnum(r.get('RoundName') or r.get('Week'))))
    return latest



CROSS_SPORT = os.environ.get('LADDER_CROSS_SPORT', '1') != '0'   # set 0 to force same-game builds
CROSS_TARGET = 2.0        # the price the ladder aims at per rung


def _combo_cross_sport(pools):
    """Best 2-3 legs from DIFFERENT sports, at one book.

    A same-game multi's legs move together - a quarterback's yards and his receiver's - so the true
    price is not the product of the parts, and the book prices that correlation in its own favour.
    Legs from different sports are independent: the chance all of them land IS the product of their
    individual rates, and the book's price is the product too. No correlation to model, no shading
    to argue with, and the number the ladder reports is the number.

    One book only, because that is what you can actually place as a single ticket.

    `pools` is {book: {sport: [legs]}}. Returns the usual combo shape, or None.
    """
    best = None
    for book, by_sport in pools.items():
        if len(by_sport) < 2:                       # needs two sports to be cross-sport
            continue
        # strongest leg per sport, by how often it has landed
        tops = []
        for sport, legs in by_sport.items():
            top = max(legs, key=lambda l: (l.get('hr') or 0, -(l.get('over') or 99)))
            if (top.get('hr') or 0) <= 0 or not top.get('over'):
                continue
            top = dict(top); top['sport'] = sport
            tops.append(top)
        if len(tops) < 2:
            continue
        tops.sort(key=lambda l: -(l.get('hr') or 0))
        # grow the ticket while it is short of the target price, keeping the safest legs first
        pick, price, hrp = [], 1.0, 1.0
        for lg in tops[:3]:
            pick.append(lg)
            price *= float(lg['over'])
            hrp *= float(lg['hr'])
            if price >= CROSS_TARGET:
                break
        if len(pick) < 2:
            continue
        cand = {'legs': pick, 'hrp': hrp, 'price': price, 'book': book, 'type': 'cross'}
        if best is None or cand['hrp'] > best['hrp']:
            best = cand
    return best


def _rank(pick):
    """Sort key for competing picks: today first, then price."""
    import datetime as _dt
    gd = str(pick.get('game_date') or '')[:10]
    try:
        ahead = (_dt.date.fromisoformat(gd) - _dt.date.today()).days if gd else 99
    except ValueError:
        ahead = 99
    return (-ahead, pick.get('odds') or pick.get('price') or 0)


def mixed_pick(bases, book=LADDER_BOOK):
    """The best build across EVERY sport at once, for the mixed challenge.

    Each sport is asked for its own best pick, and the strongest is taken - legs inside one build
    still come from one sport and one book, because that is what a bookmaker will let you place as
    a single ticket. A genuinely cross-sport multi is possible at most books, but only within one
    book's own board, so this stays honest about what it is: the best single-sport build of the day.
    """
    best = None
    for base in bases:
        if not os.path.isdir(base):
            continue
        try:
            fresh = load(os.path.join(base, 'ladder_gamelogs.json'))
            fresh = fresh if isinstance(fresh, list) else []
            gl = fresh + load_gamelogs(base)
            fx = load(os.path.join(base, 'fixture.json')) or []
            byp = build_index(gl)
            pick = best_pick(base, gl, byp, book, fx)
        except Exception as e:
            print('  mixed: %s failed (%s)' % (base, e))
            continue
        sport_name = str(base).replace('\\', '/').strip('/').split('/')[-2]
        if not pick:
            # Silence here is what made this hard to diagnose: NHL, MLB and NBL all had games on
            # the day and simply produced nothing, so NFL won by being the only sport left. Say so.
            fxn = len([g for g in (fx or []) if g.get('date')])
            print('  mixed: %-4s no pick (%d fixtures on file)' % (sport_name, fxn))
            continue
        pick['sport'] = sport_name
        print('  mixed: %-4s offers %s on %s at $%.2f (lands %.0f%%)'
              % (sport_name, pick.get('type') or '?', pick.get('game_date') or '?',
                 pick.get('price') or pick.get('odds') or 0, (pick.get('hrp') or 0) * 100))
        # Rank on WHEN before how much. Choosing purely on price let a four-days-away NFL build
        # beat everything playing that night, which is how the ladder ended up holding a bet it
        # could not settle for most of a week. A game today beats a game tomorrow at any price;
        # within the same day, the bigger price wins.
        if best is None or _rank(pick) > _rank(best):
            best = pick
    # A cross-sport ticket, built from the same legs each sport already offered. It competes with
    # the same-game builds rather than replacing them: whichever is more likely to land wins.
    if CROSS_SPORT:
        cross = _cross_from_bases(bases, book)
        if cross and (best is None or (cross.get('hrp') or 0) > (best.get('hrp') or 0)):
            print('  mixed: cross-sport build preferred (%d legs, $%.2f, lands %.0f%%)'
                  % (len(cross['legs']), cross.get('price') or 0, (cross.get('hrp') or 0) * 100))
            best = cross
    return best


def _cross_from_bases(bases, book=LADDER_BOOK):
    """Collect each sport's candidate legs and build one cross-sport ticket from them."""
    pools, meta = {}, {}
    for base in bases:
        if not os.path.isdir(base):
            continue
        sport = str(base).replace('\\', '/').strip('/').split('/')[-2]
        try:
            fresh = load(os.path.join(base, 'ladder_gamelogs.json'))
            fresh = fresh if isinstance(fresh, list) else []
            gl = fresh + load_gamelogs(base)
            fx = load(os.path.join(base, 'fixture.json')) or []
            got = best_pick(base, gl, build_index(gl), book, fx, pool_only=True)
        except Exception as e:
            print('  cross: %s failed (%s)' % (base, e))
            continue
        if not got or not got.get('pool'):
            continue
        for bk, legs in got['pool'].items():
            pools.setdefault(bk, {})[sport] = legs
        meta[sport] = got
    if not pools:
        return None
    c = _combo_cross_sport(pools)
    if not c:
        return None
    # the rung is settled per leg, so each one carries its own sport, year and round
    for lg in c['legs']:
        m = meta.get(lg.get('sport')) or {}
        lg['year'] = m.get('year')
        lg['round'] = m.get('round')
    first = meta.get(c['legs'][0].get('sport')) or {}
    return {'legs': c['legs'], 'price': round(c['price'], 2), 'odds': round(c['price'], 2),
            'book': c['book'], 'type': 'cross', 'sub': None, 'hrp': c['hrp'],
            'sport': 'mixed', 'game_date': first.get('game_date'),
            'year': first.get('year'), 'round': first.get('round')}


def main():
    if len(sys.argv) < 2:
        print('usage: build_ladder.py <data_dir> [fallback_stat]')
        print('       build_ladder.py --mixed <out_dir> <data_dir> [<data_dir> ...]')
        return
    if sys.argv[1] == '--mixed':
        return main_mixed(sys.argv[2], sys.argv[3:])
    base = sys.argv[1]
    book = sys.argv[2] if len(sys.argv) > 2 else LADDER_BOOK
    if not os.path.isdir(base):
        print('skip (no dir):', base)
        return

    fresh = load(os.path.join(base, 'ladder_gamelogs.json'))       # fitzRoy per-game feed (next-morning)
    fresh = fresh if isinstance(fresh, list) else []
    gl = fresh + load_gamelogs(base)
    if fresh:
        print('  ladder: +%d fresh player-games from fitzRoy' % len(fresh))
    fx = load(os.path.join(base, 'fixture.json')) or []
    lpath = os.path.join(base, 'ladder.json')
    lad = load(lpath) or {'bank': START, 'start': START, 'target': TARGET,
                          'attempt': 1, 'peak': START, 'days': []}
    # A hand-made or older file can carry the wrong challenge size - NHL's said it started at $100
    # while every other sport starts at $10. Normalise it, and restart a stub that never ran.
    if float(lad.get('start') or 0) != START:
        stale = not [d for d in lad.get('days', []) if d.get('result') not in (None, 'pending')]
        print('  ladder: start was $%s, resetting to $%s%s' % (lad.get('start'), START,
              ' (no graded days, starting fresh)' if stale else ''))
        lad['start'] = START
        lad['target'] = TARGET
        if stale:
            lad['bank'] = START
            lad['peak'] = START
            lad['days'] = []
            lad['attempt'] = lad.get('attempt') or 1
    byp = build_index(gl)

    # 1) grade the pending rung (all legs must clear); winnings compound, a loss busts
    for d in lad['days']:
        if d.get('result') not in (None, 'pending'):
            continue
        legs = d.get('legs') or []
        # days written before the boundary change stored "target week" = last completed + 1;
        # step back one so old and new days are graded the same way
        by, br = d.get('year', 0), d.get('round', 0)
        lr = league_round(gl)
        stale = (lr[1] - br) if lr[0] == by else (99 if lr[0] > by else 0)
        # does this sport's gamelogs carry dates? then the bet's own date is the anchor
        dated = any(str(r.get('Date') or r.get('date') or '') for r in gl[:50])
        if dated:
            newest = max((str(r.get('Date') or r.get('date') or '') for r in gl), default='')
            stale = 1 if (d.get('date') and newest and newest > d.get('date')) else 0
        outcomes = []
        for lg in legs:
            fld = MKT.get(lg.get('market'), lg.get('market'))
            outcomes.append(grade_leg(byp, lg.get('pick_name') or lg.get('name'), fld,
                                      lg.get('line'), by, br, stale, dated and d.get('date')))
        # A day that cannot be graded must not freeze the ladder. The round-based void needs the
        # league to move on, which never happens once a season ends - AFL and NFL both sat pending
        # from 20 September, so no new rung was ever added. After STALE_DAYS the day is written off
        # as a void, the bank is untouched and the next run picks a fresh rung.
        # measure from the day the bet could be DECIDED, falling back to the day it was struck
        _from = str(d.get('game_date') or d.get('date') or '')[:10]
        try:
            age = (datetime.date.today() - datetime.date.fromisoformat(_from)).days
        except (TypeError, ValueError):
            age = 0
        # The clock runs from the day the bet was STRUCK, which is not the day it is decided. An
        # NFL bet struck on the Monday for a game played the following Thursday is already four
        # days old at kick-off, so a winning rung was being written off before the ball was thrown.
        # If not one row exists for that round yet, the game has not been played and no amount of
        # waiting makes the day stale - hold it. Once rows for the round DO exist and this player
        # still has none, the round-based void below handles it properly.
        round_played = False
        if not dated and d.get('year') and d.get('round') is not None:
            want = (int(d['year']), rnum(d['round']))
            for r in gl:
                try:
                    if (int(r.get('Year', 0) or 0), rnum(r.get('RoundName') or r.get('Week'))) == want:
                        round_played = True
                        break
                except (TypeError, ValueError):
                    continue
            if not round_played:
                print('  ladder: %s held - round %s has not been played yet' % (d.get('date'), d.get('round')))
                continue
        if age > STALE_DAYS and (not outcomes or any(o is None for o in outcomes)):
            print('  ladder: %s voided - still ungraded after %s days' % (d.get('date'), age))
            d['result'] = 'void'
            d['bank_after'] = d.get('bank_before', lad['bank'])
            continue
        if DNP in outcomes:
            # a leg whose player never took the field is a void, not a loss: drop the day and let
            # today's run pick a fresh rung at the same bank
            print('  ladder: %s voided - %s did not play' % (d.get('date'),
                  ', '.join(lg.get('name') for lg, o in zip(legs, outcomes) if o == DNP)))
            d['result'] = 'void'
            d['bank_after'] = d.get('bank_before', lad['bank'])
            continue
        if not outcomes or any(o is None for o in outcomes):
            continue                              # not all legs played yet
        before = d.get('bank_before', lad['bank'])
        if all(outcomes):
            d['result'] = 'win'
            d['bank_after'] = round(before * d['odds'], 2)
            lad['bank'] = d['bank_after']
            lad['peak'] = max(lad.get('peak', START), lad['bank'])
            if lad['bank'] >= lad.get('target', TARGET):
                d['complete'] = True              # reached the top of the ladder
        else:
            d['result'] = 'loss'
            d['bank_after'] = 0.0                 # busted

    # 2) add the next rung once the latest is graded
    last = lad['days'][-1] if lad['days'] else None
    if (last is None) or last.get('result') in ('win', 'loss'):
        if last is None:
            bank, rung = START, 1
        elif last['result'] == 'win' and not last.get('complete'):
            bank, rung = lad['bank'], last['rung'] + 1      # climb: stake the whole balance
        else:                                                # busted or topped out -> new climb
            bank, rung = START, 1
            lad['attempt'] = lad.get('attempt', 1) + 1
            lad['days'] = []
        lad['bank'] = bank
        sgm = best_pick(base, gl, byp, book, fx)
        if sgm:
            legs = [{'name': lg['name'], 'pick_name': lg['name'], 'market': lg['market'],
                     'sport': lg.get('sport'), 'year': lg.get('year'), 'round': lg.get('round'),
                     'line': lg['line'], 'odds': lg['over'], 'team': lg.get('team'),
                     'opp': lg.get('opp')} for lg in sgm['legs']]
            desc = ' + '.join('%s %s+ %s' % (lg['name'].split(' ')[-1], lg['line'], lg['market'])
                              for lg in sgm['legs'])
            sb = sgm.get('sub')
            sub_rec = ({'name': sb['name'], 'market': sb['market'], 'line': sb['line'],
                        'odds': sb['over'], 'team': sb.get('team'), 'opp': sb.get('opp'),
                        'hr': round(sb.get('hr', 0), 2)} if sb else None)
            lad['days'].append({'date': datetime.date.today().isoformat(), 'rung': rung,
                                'legs': legs, 'pick': desc, 'odds': sgm['price'],
                                'book': sgm['book'], 'type': sgm.get('type'), 'sub': sub_rec,
                                'bank_before': round(bank, 2), 'bank_after': None,
                                'result': 'pending', 'year': sgm['year'], 'round': sgm['round'],
                                'game_date': sgm.get('game_date')})

    lad['days'] = lad['days'][-MAX_RUNGS:]
    with open(lpath, 'w') as fh:
        json.dump(lad, fh, indent=2)
    tail = lad['days'][-1] if lad['days'] else {}
    print('ladder %s: attempt %s, rung %s, bank $%s (peak $%s), latest: %s @ $%s' %
          (base, lad.get('attempt', 1), tail.get('rung', 0), lad['bank'], lad.get('peak', START),
           (tail.get('pick') or '-')[:60], tail.get('odds')))


def main_mixed(out_dir, bases):
    """One challenge whose day can come from ANY sport - the best build on the board that day.

    Each day records which sport it came from, so grading loads that sport's gamelogs and nothing
    else. Same $10 start, same compounding, same bust rule as a single-sport ladder.
    """
    if not bases:
        print('mixed: no sport dirs given')
        return
    lpath = os.path.join(out_dir, 'ladder.json')
    lad = load(lpath) or {'bank': START, 'start': START, 'target': TARGET,
                          'attempt': 1, 'peak': START, 'days': [], 'mixed': True}
    lad['mixed'] = True
    if float(lad.get('start') or 0) != START:
        lad['start'] = START; lad['target'] = TARGET

    idx_cache = {}
    def index_for(sport):
        if sport in idx_cache:
            return idx_cache[sport]
        base = next((b for b in bases if str(b).replace('\\', '/').strip('/').split('/')[-2] == sport), None)
        gl = []
        if base and os.path.isdir(base):
            fresh = load(os.path.join(base, 'ladder_gamelogs.json'))
            gl = (fresh if isinstance(fresh, list) else []) + load_gamelogs(base)
        idx_cache[sport] = build_index(gl)
        idx_cache['_gl_' + sport] = gl
        return idx_cache[sport]

    # 1) grade any pending day, against the sport that day came from
    for d in lad['days']:
        if d.get('result') not in (None, 'pending'):
            continue
        outcomes = []
        for lg in (d.get('legs') or []):
            # A cross-sport ticket has no single sport: each leg is settled against ITS OWN
            # league's gamelogs and its own round, which is the whole reason the legs are
            # independent in the first place.
            sp = lg.get('sport') or d.get('sport') or ''
            byp = index_for(sp)
            fld = MKT.get(lg.get('market'), lg.get('market'))
            gl2 = (idx_cache.get('_gl_' + sp) or [])
            dated2 = any(str(r.get('Date') or r.get('date') or '') for r in gl2[:50])
            newest2 = max((str(r.get('Date') or r.get('date') or '') for r in gl2), default='')
            ref = str(d.get('game_date') or d.get('date') or '')[:10]
            stale2 = 1 if (dated2 and ref and newest2 > ref) else 0
            outcomes.append(grade_leg(byp, lg.get('pick_name') or lg.get('name'), fld,
                                      lg.get('line'),
                                      lg.get('year', d.get('year', 0)) or 0,
                                      lg.get('round', d.get('round', 0)) or 0,
                                      stale2, dated2 and ref))
        try:
            age2 = (datetime.date.today() - datetime.date.fromisoformat(str(d.get('date'))[:10])).days
        except (TypeError, ValueError):
            age2 = 0
        if age2 > STALE_DAYS and (not outcomes or any(o is None for o in outcomes)):
            print('  mixed: %s voided - still ungraded after %s days' % (d.get('date'), age2))
            d['result'] = 'void'
            d['bank_after'] = d.get('bank_before', lad['bank'])
            continue
        if not outcomes or any(o is None for o in outcomes):
            continue
        before = d.get('bank_before', lad['bank'])
        if all(outcomes):
            d['result'] = 'win'; d['bank_after'] = round(before * d['odds'], 2)
            lad['bank'] = d['bank_after']; lad['peak'] = max(lad.get('peak', START), lad['bank'])
            if lad['bank'] >= lad.get('target', TARGET):
                d['complete'] = True
        else:
            d['result'] = 'loss'; d['bank_after'] = 0.0

    # 2) add today's rung once the last one is graded
    last = lad['days'][-1] if lad['days'] else None
    if last is None or last.get('result') not in (None, 'pending'):
        if last is None:
            bank, rung = lad.get('bank', START), 1
        elif last.get('result') == 'void':
            bank, rung = last.get('bank_after', lad['bank']), last.get('rung', 1)   # same rung, same bank
            lad['days'] = [x for x in lad['days'] if x is not last]
        elif last.get('result') == 'win' and not last.get('complete'):
            bank, rung = lad['bank'], last.get('rung', 1) + 1
        else:
            bank, rung = START, 1
            lad['attempt'] = lad.get('attempt', 1) + 1
            lad['days'] = []
        lad['bank'] = bank
        pick = mixed_pick(bases)
        if pick:
            legs = [{'name': lg['name'], 'pick_name': lg['name'], 'market': lg['market'],
                     'line': lg['line'], 'odds': lg['over'], 'team': lg.get('team'),
                     'opp': lg.get('opp')} for lg in pick['legs']]
            desc = ' + '.join('%s %s+ %s' % (lg['name'].split(' ')[-1], lg['line'], lg['market'])
                              for lg in pick['legs'])
            lad['days'].append({'date': datetime.date.today().isoformat(), 'rung': rung,
                                'sport': pick.get('sport'), 'legs': legs, 'pick': desc,
                                'odds': pick['price'], 'book': pick['book'], 'type': pick.get('type'),
                                'bank_before': round(bank, 2), 'bank_after': None,
                                'result': 'pending', 'year': pick['year'], 'round': pick['round']})

    lad['days'] = lad['days'][-MAX_RUNGS:]
    os.makedirs(out_dir, exist_ok=True)
    with open(lpath, 'w') as fh:
        json.dump(lad, fh, indent=2)
    tail = lad['days'][-1] if lad['days'] else {}
    print('ladder MIXED: attempt %s, rung %s, bank $%s, from %s, latest: %s @ $%s' %
          (lad.get('attempt', 1), tail.get('rung', 0), lad['bank'], tail.get('sport', '-'),
           (tail.get('pick') or '-')[:50], tail.get('odds')))


if __name__ == '__main__':
    main()
