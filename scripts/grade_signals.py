#!/usr/bin/env python3
"""Grade the signal ledger and report how each tile has actually done.

    python scripts/grade_signals.py [--log data/signal_log.json] [--root .] [--stake 1]

Every row in the ledger was captured at a fixed time before its game (see snapshot_signals.js).
This settles the ones whose game has been played, by looking the player up in that sport's own
gamelogs, and then reports per TILE: bets, hit rate, units. That per-tile line is the number worth
publishing - "Green Lights: 118 bets, 61%, +4.2u" says more than any feature list, and it is also
how a tile that does not work gets found and retired.

A bet is settled only when the player has a row for that game. A player who did not take the field
is VOID, not a loss: the signal said what he would do if he played.
"""
import argparse
import datetime
import json
import os
import sys
from collections import defaultdict

SPORT_DIR = {'afl': 'AFL', 'nfl': 'nfl', 'epl': 'EPL', 'mlb': 'mlb', 'nbl': 'nbl', 'nhl': 'nhl'}


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


STALE_DAYS = 2      # after this, a bet with no gamelog row is written off rather than left open


def settle(bet, logs_by_player):
    """win / loss / void for one logged bet, or None while the game is still to be played."""
    rows = logs_by_player.get(norm(bet.get('player')), [])
    day = str(bet.get('start') or '')[:10]
    played = [r for r in rows if str(r.get('Date') or r.get('date') or '')[:10] == day]
    if not played:
        # No row for that day. Either the game has not been played, or he did not take the field -
        # which is a VOID, not a loss: the signal said what he would do IF he played. Only call it
        # once the game is safely behind us, so a pending game is never written off early.
        try:
            age = (datetime.date.today() - datetime.date.fromisoformat(day)).days
        except (TypeError, ValueError):
            return None, None
        return ('void', None) if age > STALE_DAYS else (None, None)
    v = None
    for r in played:
        got = stat_of(r, bet.get('market'))
        if got is not None:
            v = (v or 0) + float(got)
    if v is None:
        return 'void', None                      # he played, but the feed does not carry that stat
    line = bet.get('line')
    if line is None:
        return None, v
    if bet.get('side') == 'under':
        return ('win' if v < line else 'loss'), v
    return ('win' if v > line else 'loss'), v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--log', default='data/signal_log.json')
    ap.add_argument('--root', default='.')
    ap.add_argument('--stake', type=float, default=1.0)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()

    log = load(a.log, {'rows': []}) or {'rows': []}
    rows = log.get('rows') or []
    if not rows:
        print('signal ledger is empty - nothing to grade')
        return

    by_sport = defaultdict(list)
    for b in rows:
        if b.get('result') is None:
            by_sport[b.get('sport')].append(b)

    settled = 0
    for sport, bets in by_sport.items():
        logs = gamelogs_for(a.root, sport)
        if not logs:
            continue
        idx = defaultdict(list)
        for r in logs:
            idx[norm(r.get('Player') or r.get('player') or r.get('name'))].append(r)
        for b in bets:
            res, actual = settle(b, idx)
            if res:
                b['result'], b['actual'] = res, actual
                settled += 1

    # ---- the report: per tile, which is the point of keeping the ledger ----
    tiles = defaultdict(lambda: {'n': 0, 'w': 0, 'l': 0, 'v': 0, 'u': 0.0})
    for b in rows:
        if not b.get('result'):
            continue
        t = tiles[(b.get('sport'), b.get('tile'))]
        t['n'] += 1
        if b['result'] == 'win':
            t['w'] += 1
            t['u'] += (float(b.get('price') or 1) - 1) * a.stake
        elif b['result'] == 'loss':
            t['l'] += 1
            t['u'] -= a.stake
        else:
            t['v'] += 1

    print('%-6s %-22s %5s %6s %8s %8s' % ('sport', 'tile', 'bets', 'hit', 'units', 'roi'))
    order = sorted(tiles.items(), key=lambda kv: -kv[1]['u'])
    for (sport, tile), t in order:
        decided = t['w'] + t['l']
        hit = ('%.0f%%' % (t['w'] / decided * 100)) if decided else '-'
        staked = decided * a.stake
        roi = ('%+.1f%%' % (t['u'] / staked * 100)) if staked else '-'
        print('%-6s %-22s %5d %6s %+8.2f %8s' % (sport, (tile or '?')[:22], t['n'], hit, t['u'], roi))

    tot_u = sum(t['u'] for t in tiles.values())
    tot_d = sum(t['w'] + t['l'] for t in tiles.values())
    print('-' * 60)
    print('%-29s %5d %6s %+8.2f' % ('all tiles', sum(t['n'] for t in tiles.values()),
          ('%.0f%%' % (sum(t['w'] for t in tiles.values()) / tot_d * 100)) if tot_d else '-', tot_u))
    print('settled this run: %d | still pending: %d'
          % (settled, sum(1 for b in rows if not b.get('result'))))

    with open(a.log, 'w') as fh:
        json.dump(log, fh)
    if a.out:
        report = [{'sport': s, 'tile': t, **v} for (s, t), v in order]
        os.makedirs(os.path.dirname(a.out) or '.', exist_ok=True)
        with open(a.out, 'w') as fh:
            json.dump({'updated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
                       'tiles': report}, fh)
        print('wrote ' + a.out)


if __name__ == '__main__':
    main()
