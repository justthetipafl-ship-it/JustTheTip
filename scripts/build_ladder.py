#!/usr/bin/env python3
"""JTT shared ladder challenge -> writes <base>/ladder.json.
Each run: grade any pending pick whose next game has been played, adjust the bankroll,
then add the next day's banker (the safest 'lock' line). Keeps a rolling 10 days."""
import json
import os
import re
import sys
import datetime

STAT = 'disposals'
STAKE = 10.0
KEEP = 10
NOM_ODDS = 1.30
MIN_LINE = 12


def load(path):
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    return None


def rnum(name):
    m = re.search(r'(\d+)', name or '')
    return int(m.group(1)) if m else 0


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else 'AFL/data'
    gl = load(os.path.join(base, 'gamelogs.json')) or []
    fx = load(os.path.join(base, 'fixture.json')) or []
    lpath = os.path.join(base, 'ladder.json')
    lad = load(lpath) or {'bank': 100.0, 'start': 100.0, 'days': []}

    byp = {}
    for r in gl:
        v = r.get(STAT)
        if v is None:
            continue
        rec = byp.setdefault(r['Player'], {'team': r.get('Team'), 'games': []})
        rec['team'] = r.get('Team')
        rec['games'].append((int(r.get('Year', 0)), rnum(r.get('RoundName')), float(v)))
    for nm in byp:
        byp[nm]['games'].sort()

    for d in lad['days']:
        if d.get('result') not in (None, 'pending'):
            continue
        rec = byp.get(d.get('pick_name'))
        if not rec:
            continue
        after = [(y, rd, v) for (y, rd, v) in rec['games']
                 if (y, rd) >= (d.get('year', 0), d.get('round', 0))]
        if after:
            y, rd, v = after[0]
            d['result'] = 'win' if v >= d['line'] else 'loss'
            if d['result'] == 'win':
                lad['bank'] = round(lad['bank'] + STAKE * (d['odds'] - 1), 2)
            else:
                lad['bank'] = round(lad['bank'] - STAKE, 2)
            d['bank'] = lad['bank']

    last = lad['days'][-1] if lad['days'] else None
    if (last is None) or last.get('result') in ('win', 'loss'):
        teams = set()
        for g in fx:
            if g.get('home') and g.get('away'):
                teams.add(g['home'])
                teams.add(g['away'])
        best = None
        for nm, rec in byp.items():
            if teams and rec['team'] not in teams:
                continue
            games = rec['games']
            if len(games) < 6:
                continue
            recent = [v for (_, _, v) in games[-8:]]
            line = float(int(min(recent))) - 1.5
            if line < MIN_LINE:
                continue
            if (best is None) or line > best['line']:
                y, rd, _ = games[-1]
                best = {'name': nm, 'line': line, 'year': y, 'round': rd + 1}
        if best:
            lad['days'].append({
                'date': datetime.date.today().isoformat(),
                'pick': best['name'] + ' ' + str(best['line']) + '+ ' + STAT,
                'pick_name': best['name'],
                'line': best['line'],
                'market': STAT,
                'odds': NOM_ODDS,
                'result': 'pending',
                'bank': None,
                'year': best['year'],
                'round': best['round'],
            })

    lad['days'] = lad['days'][-KEEP:]
    with open(lpath, 'w') as fh:
        json.dump(lad, fh, indent=2)
    print('ladder updated:', len(lad['days']), 'days, bank', lad['bank'])


if __name__ == '__main__':
    main()
