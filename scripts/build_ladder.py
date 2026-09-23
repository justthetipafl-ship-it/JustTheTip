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


def best_pick(base, gl, byp, book=LADDER_BOOK):
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
    elif playable:                                       # else the SOONEST upcoming slate, so the ladder keeps climbing between game days
        soonest = min(_gdate(g) for g in playable if _gdate(g))
        slate = [g for g in playable if _gdate(g) == soonest]
    else:
        return None
    games = [(g.get('home'), g.get('away')) for g in slate]
    if not games:
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
    return {'legs': best['legs'], 'price': round(best['price'], 2), 'book': best['book'],
            'type': best['type'], 'sub': subleg, 'year': latest[0], 'round': latest[1] + 1}



def grade_leg(byp, name, field, line, year, rnd):
    rec = byp.get(name)
    if not rec:
        return None
    for r in rec['games']:
        y = int(r.get('Year', 0) or 0)
        rd = rnum(r.get('RoundName') or r.get('Week'))
        if (y, rd) >= (year, rnd) and r.get(field) is not None:
            return float(r.get(field)) >= line
    return None


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
            pick = best_pick(base, gl, byp, book)
        except Exception as e:
            print('  mixed: %s failed (%s)' % (base, e))
            continue
        if not pick:
            continue
        pick['sport'] = str(base).replace('\\', '/').strip('/').split('/')[-2]
        if best is None or (pick.get('odds') or 0) > (best.get('odds') or 0):
            best = pick
    return best


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
        outcomes = []
        for lg in legs:
            fld = MKT.get(lg.get('market'), lg.get('market'))
            outcomes.append(grade_leg(byp, lg.get('pick_name') or lg.get('name'), fld,
                                      lg.get('line'), d.get('year', 0), d.get('round', 0)))
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
        sgm = best_pick(base, gl, byp, book)
        if sgm:
            legs = [{'name': lg['name'], 'pick_name': lg['name'], 'market': lg['market'],
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
                                'result': 'pending', 'year': sgm['year'], 'round': sgm['round']})

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
        return idx_cache[sport]

    # 1) grade any pending day, against the sport that day came from
    for d in lad['days']:
        if d.get('result') not in (None, 'pending'):
            continue
        byp = index_for(d.get('sport') or '')
        outcomes = []
        for lg in (d.get('legs') or []):
            fld = MKT.get(lg.get('market'), lg.get('market'))
            outcomes.append(grade_leg(byp, lg.get('pick_name') or lg.get('name'), fld,
                                      lg.get('line'), d.get('year', 0), d.get('round', 0)))
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
