#!/usr/bin/env python3
"""Every market key the odds feed carries for each sport we use, and the credit balance.

    ROA_API_KEY=... python scripts/list_markets.py [--out data/odds_markets.json]

WHY
scripts/fetch_odds.py asks the feed for a hand-written list of markets. Whether the feed carries
anything ELSE - a double-double market, an NHL saves line - could only be found by guessing a key
and spending credits to see if it answered. The feed publishes its own list, free, and this
writes it down next to the list we actually ask for, so the gap between the two is a file anyone
can read instead of a question.

Both calls here are free: /sports with markets, and /usage.

WHAT IS WRITTEN
data/odds_markets.json

    {"updated":"...","usage":{"credits_used":..,"credits_limit":..,"credits_remaining":..},
     "sports":{"NBA":{"game":[..],"team":[..],"player_props":[..],
                      "asked":[..keys fetch_odds.py requests..],
                      "notAsked":[..player props on offer that we do not request..]}}}
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SPORTS = ['NBA', 'NFL', 'NHL', 'NBL', 'EPL', 'MLB']


def asked_for(sport):
    """The market keys fetch_odds.py requests for a sport, milestones suffix removed."""
    try:
        import fetch_odds as F
    except Exception:
        return []
    keys = set(F.SPORTS.get(sport, {}).keys() if isinstance(F.SPORTS.get(sport), dict) else [])
    keys |= set(getattr(F, 'FIELD_BY_SPORT', {}).get(sport, []))
    keys |= set(getattr(F, 'GAME_MARKETS', {}).get(sport, []))
    return sorted(keys)


def gap(player_props, asked):
    """Player props the feed offers that we do not ask for. A '_milestones' twin of a market we
    do ask for is not a gap - fetch_odds.py adds those itself."""
    have = set(asked)
    out = []
    for k in player_props or []:
        base = k[:-len('_milestones')] if k.endswith('_milestones') else k
        if base not in have and k not in have:
            out.append(k)
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='data/odds_markets.json')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        ok = gap(['player_points', 'player_points_milestones', 'player_double_double'],
                 ['player_points']) == ['player_double_double']
        ok = ok and gap([], ['x']) == [] and gap(None, []) == []
        print('SELFTEST', 'PASSED' if ok else 'FAILED')
        return 0 if ok else 1
    key = os.environ.get('ROA_API_KEY')
    if not key:
        sys.exit('ROA_API_KEY not set')
    from rapidoddsapi import RapidOddsAPI
    client = RapidOddsAPI(api_key=key)
    out = {'updated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
           'usage': None, 'sports': {}}
    try:
        u = client.get_usage()
        out['usage'] = {k: u.get(k) for k in ('tier', 'credits_used', 'credits_limit',
                                              'credits_remaining', 'resets')}
    except Exception as e:
        print('usage failed: %s' % e)
    for sp in SPORTS:
        try:
            info = client.get_sport(sp)
        except Exception as e:
            print('%s: %s' % (sp, e))
            continue
        m = info.get('markets') or {}
        asked = asked_for(sp)
        rec = {'game': sorted(m.get('game') or []), 'team': sorted(m.get('team') or []),
               'player_props': sorted(m.get('player_props') or []), 'asked': asked}
        rec['notAsked'] = gap(rec['player_props'], asked)
        out['sports'][sp] = rec
        print('%s: %d game, %d team, %d player props; not asked for: %s'
              % (sp, len(rec['game']), len(rec['team']), len(rec['player_props']),
                 ', '.join(rec['notAsked']) or 'none'))
    if not out['sports']:
        sys.exit('no sport answered - keeping the file already there')
    os.makedirs(os.path.dirname(a.out) or '.', exist_ok=True)
    with open(a.out, 'w') as f:
        json.dump(out, f, indent=1)
    print('wrote %s' % a.out)
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
