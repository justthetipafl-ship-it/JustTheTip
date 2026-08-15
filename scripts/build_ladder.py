#!/usr/bin/env python3
"""JTT shared ladder challenge -> writes <base>/ladder.json for one sport.
Usage: build_ladder.py <data_dir>

Preferred mode (odds present): each day's pick is the best ~$2 same-game multi
(2-4 legs, any stat) from a single bookmaker, chosen by highest combined hit-rate.
Fallback mode (no odds, e.g. AFLW): a single-stat banker on [fallback_stat].

Each run grades the previous pending pick (all legs must clear), moves the bankroll,
then adds the next pick. Keeps a rolling 10 days. Handles split game-log files."""
import json
import os
import re
import sys
import glob
import datetime
from itertools import combinations, product

START = 10.0
TARGET = 10000.0
MAX_RUNGS = 12
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
       'RBI': 'RBI', 'R': 'R', 'SB': 'SB', 'K': 'SO'}


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
    return byp


def hit_rate(rec, field, line):
    vals = [r.get(field) for r in rec['games'][-HIT_WINDOW:] if r.get(field) is not None]
    vals = [float(v) for v in vals]
    if len(vals) < 4:
        return None
    return sum(1 for v in vals if v >= line) / float(len(vals))


def _mkleg(l, byp):
    nm = l.get('player'); mk = l.get('market'); ov = l.get('over')
    if nm is None or ov is None or mk not in MKT or ov < LEG_LO or ov > LEG_HI:
        return None
    rec = byp.get(nm)
    if not rec or len(rec['games']) < MIN_GAMES:
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
        games[gi] = sorted(byline.values(), key=lambda x: -x['hr'])[:4]
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


def best_pick(base, gl, byp):
    """Cross-game multi across the slate (one book); SGM only when a single game is on."""
    od = load(os.path.join(base, 'odds.json'))
    fx = load(os.path.join(base, 'fixture.json')) or []
    if not od or not od.get('lines'):
        return None
    legs_all = list(od.get('lines') or []) + list(od.get('alt') or [])

    pteam = {nm: rec['team'] for nm, rec in byp.items()}
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    def _upcoming(g):
        u = g.get('utc')
        if u:
            try:
                return datetime.datetime.fromisoformat(str(u).replace('Z', '')) > now
            except Exception:
                return True
        d = g.get('date')
        if d:
            return str(d) >= now.strftime('%Y-%m-%d')
        return True
    games = [(g.get('home'), g.get('away')) for g in fx
             if g.get('home') and g.get('away') and _upcoming(g)]
    if not games:
        return None
    team_game = {}
    for i, (h, a) in enumerate(games):
        team_game[h] = i; team_game[a] = i
    single = len(games) == 1

    # priced, gradeable, short legs grouped by (book, game)
    by_book = {}
    for l in legs_all:
        gi = team_game.get(pteam.get(l.get('player')))
        if gi is None:
            continue
        lg = _mkleg(l, byp)
        if lg is None:
            continue
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
    latest = (0, 0)
    for lg in best['legs']:
        g = byp[lg['name']]['games'][-1]
        latest = max(latest, (int(g.get('Year', 0) or 0), rnum(g.get('RoundName') or g.get('Week'))))
    return {'legs': best['legs'], 'price': round(best['price'], 2), 'book': best['book'],
            'type': best['type'], 'year': latest[0], 'round': latest[1] + 1}



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


def main():
    if len(sys.argv) < 2:
        print('usage: build_ladder.py <data_dir> [fallback_stat]')
        return
    base = sys.argv[1]
    if not os.path.isdir(base):
        print('skip (no dir):', base)
        return

    gl = load_gamelogs(base)
    fx = load(os.path.join(base, 'fixture.json')) or []
    lpath = os.path.join(base, 'ladder.json')
    lad = load(lpath) or {'bank': START, 'start': START, 'target': TARGET,
                          'attempt': 1, 'peak': START, 'days': []}
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
        sgm = best_pick(base, gl, byp)
        if sgm:
            legs = [{'name': lg['name'], 'pick_name': lg['name'], 'market': lg['market'],
                     'line': lg['line'], 'odds': lg['over']} for lg in sgm['legs']]
            desc = ' + '.join('%s %s+ %s' % (lg['name'].split(' ')[-1], lg['line'], lg['market'])
                              for lg in sgm['legs'])
            lad['days'].append({'date': datetime.date.today().isoformat(), 'rung': rung,
                                'legs': legs, 'pick': desc, 'odds': sgm['price'],
                                'book': sgm['book'], 'type': sgm.get('type'),
                                'bank_before': round(bank, 2), 'bank_after': None,
                                'result': 'pending', 'year': sgm['year'], 'round': sgm['round']})

    lad['days'] = lad['days'][-MAX_RUNGS:]
    with open(lpath, 'w') as fh:
        json.dump(lad, fh, indent=2)
    tail = lad['days'][-1] if lad['days'] else {}
    print('ladder %s: attempt %s, rung %s, bank $%s (peak $%s), latest: %s @ $%s' %
          (base, lad.get('attempt', 1), tail.get('rung', 0), lad['bank'], lad.get('peak', START),
           (tail.get('pick') or '-')[:60], tail.get('odds')))


if __name__ == '__main__':
    main()
