#!/usr/bin/env python3
"""JTT shared ladder challenge -> writes <base>/ladder.json for one sport.
Usage: build_ladder.py <data_dir> <stat>
Each run: grade any pending pick whose next game has been played, adjust the bankroll,
then add the next day's banker (the safest 'lock' line). Keeps a rolling 10 days.
Handles single-file (gamelogs.json) and per-season (gamelogs_YYYY.json) game logs."""
import json
import os
import re
import sys
import glob
import datetime

STAKE = 10.0
KEEP = 10
NOM_ODDS = 1.30
MIN_GAMES = 6


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


def main():
    if len(sys.argv) < 2:
        print('usage: build_ladder.py <data_dir> <stat>')
        return
    base = sys.argv[1]
    stat = sys.argv[2] if len(sys.argv) > 2 else 'disposals'
    if not os.path.isdir(base):
        print('skip (no dir):', base)
        return

    gl = load_gamelogs(base)
    fx = load(os.path.join(base, 'fixture.json')) or []
    lpath = os.path.join(base, 'ladder.json')
    lad = load(lpath) or {'bank': 100.0, 'start': 100.0, 'stat': stat, 'days': []}
    lad['stat'] = stat

    byp = {}
    for r in gl:
        v = r.get(stat)
        if v is None:
            continue
        try:
            v = float(v)
        except Exception:
            continue
        rec = byp.setdefault(r.get('Player'), {'team': r.get('Team'), 'games': []})
        rec['team'] = r.get('Team')
        rec['games'].append((int(r.get('Year', 0) or 0), rnum(r.get('RoundName') or r.get('Week')), v))
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
            for key in ('home', 'away', 'homeAbbr', 'awayAbbr'):
                if g.get(key):
                    teams.add(g[key])
        cands = [nm for nm, rec in byp.items()
                 if len(rec['games']) >= MIN_GAMES and (not teams or rec['team'] in teams)]
        if not cands:
            latest = max((rec['games'][-1][:2] for rec in byp.values() if rec['games']), default=(0, 0))
            cands = [nm for nm, rec in byp.items()
                     if len(rec['games']) >= MIN_GAMES and rec['games'][-1][:2] >= (latest[0], latest[1] - 1)]
        best = None
        for nm in cands:
            rec = byp[nm]
            recent = [v for (_, _, v) in rec['games'][-8:]]
            mn = min(recent)
            line = float(int(mn * 0.85)) + 0.5
            if line < 0.5:
                continue
            if (best is None) or line > best['line']:
                y, rd, _ = rec['games'][-1]
                best = {'name': nm, 'line': line, 'year': y, 'round': rd + 1}
        if best:
            lad['days'].append({
                'date': datetime.date.today().isoformat(),
                'pick': best['name'] + ' ' + str(best['line']) + '+ ' + stat,
                'pick_name': best['name'],
                'line': best['line'],
                'market': stat,
                'odds': NOM_ODDS,
                'result': 'pending',
                'bank': None,
                'year': best['year'],
                'round': best['round'],
            })

    lad['days'] = lad['days'][-KEEP:]
    with open(lpath, 'w') as fh:
        json.dump(lad, fh, indent=2)
    print('ladder %s (%s): %d days, bank %s' % (base, stat, len(lad['days']), lad['bank']))


if __name__ == '__main__':
    main()
