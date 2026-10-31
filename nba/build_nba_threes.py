#!/usr/bin/env python3
"""From deep: what kind of shooter he is, and how much of his line you can actually forecast.

    python nba/build_nba_threes.py --out nba/data [--seasons 2026,2027]
    python nba/build_nba_threes.py --selftest        # no network, no files

WHY THIS CARD SPENDS MOST OF ITS SPACE ON WHAT NOT TO TRUST
A threes line looks like a bet on shooting and is almost entirely a bet on volume. Split-half
over 290 shooters - his first half-season against his second:

    attempts per game      r = +0.845
    threes made            r = +0.787
    three-point PERCENT    r = +0.293
    minutes                r = +0.802

How many he lets go persists. How well he shoots barely does.

AND A HOT STREAK IS A WARNING. Over 15,048 player-games, taking his last five games against his
own season rate:

    recent 3P%     -> next game's 3P%        r = -0.0275
    recent makes   -> next game's makes      r = +0.0376
    recent ATTEMPTS-> next game's attempts   r = +0.5222

    hottest fifth by recent 3P%: next game 1.9 points BELOW his own rate
    coldest fifth:                          +0.1 points

The most common reason anyone takes a threes over - he is on fire from deep - is mean reversion,
and it points slightly the wrong way. "He has been shooting a lot lately" is the real one. The
card says this in plain words, because it is the single most useful thing on it.

WHAT KIND OF SHOOTER HE IS, which is real and is not a forecast.
85.3% of made threes in 2026 were assisted, and per shooter that runs from 29% to 100% - Shai
Gilgeous-Alexander makes his own, Onyeka Okongwu and Myles Turner have every one created for
them. That is a genuine axis and it is on the card.

It is NOT a read on absences, and the card says so. The obvious hypothesis - a catch-and-shoot
specialist collapses when his creator sits - was tested and failed exactly as the passing network
did: top feeder out moved a shooter by +0.006 threes (median -0.058) against a +0.084 control,
52% went down, and the >=90%-assisted group came out at +0.024, no worse than the self-creators
at -0.014. Someone else creates and the shots still come.

THE OPPONENT IS THE WEAKEST OF THE THREE CARDS. Out of sample (last season's table against this
season's games): attempts allowed 0.21 of a three between the softest and toughest third, threes
allowed 0.17, pace 0.20, and their three-point percentage ALLOWED r=-0.016 - nothing at all,
which is the long-known result that a defence controls how many threes you take far more than
whether they go in. The card prints the attempts and refuses to rank the percentage.

WHERE he shoots from is already in zones.json (cornerThree / armsThree, per player and per
defence) so it is not recomputed here; the page reads it from there.

WHAT IS WRITTEN
nba/data/threes.json

    {"league": {"made": 1.42, "att": 3.91, "pct": 0.363, "assistedShare": 0.853,
                "stability": {"att": 0.845, "made": 0.787, "pct": 0.293},
                "recent": {"attToAtt": 0.522, "pctToPct": -0.0275, "madeToMade": 0.0376,
                           "hotFifthPts": -1.9},
                "oppSwing": {"att": 0.21, "made": 0.17, "pct": -0.016}},
     "defence": {"BOS": {"made": 13.1, "madeRank": 9, "att": 36.4, "attRank": 7,
                         "pct": 0.359, "of": 30}},
     "players": [{"playerId": "...", "name": "...", "team": "BOS", "games": 71,
                  "minutes": 33.1, "made": 3.2, "att": 8.4, "pct": 0.381,
                  "assistedShare": 0.92,
                  "creators": [{"id": "...", "name": "...", "n": 44, "share": 0.29}]}]}
"""
import argparse
import collections
import datetime
import io
import json
import os
import statistics
import sys
import urllib.request

REL = 'https://github.com/sportsdataverse/sportsdataverse-data/releases/download'
PBP = REL + '/espn_nba_pbp/play_by_play_%s.parquet'

MIN_GAMES = 10          # below this his rates are a handful of nights
MIN_ATT = 1.0           # and below one attempt a game he is not a threes read
MIN_MADE_PBP = 15       # made threes on file before an assisted share means anything
MAX_CREATORS = 4
MIN_CREATOR_SHARE = 0.05
MIN_DEF_GAMES = 20


def fnum(v):
    try:
        f = float(v)
        return 0.0 if f != f else f
    except (TypeError, ValueError):
        return 0.0


def nm(v):
    """A name, never a float - same guard as the other NBA builds. A NaN reaching json.dump is
    written as a bare NaN, which is not JSON, and the page drops the whole file in a .catch."""
    if v is None:
        return ''
    try:
        if v != v:
            return ''
    except Exception:
        return ''
    t = str(v).strip()
    return '' if t.lower() in ('nan', 'none') else t


def pid(v):
    t = str(v).strip()
    if t.endswith('.0'):
        t = t[:-2]
    return t if t.isdigit() else ''


def grab(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (JTT build)'})
    with urllib.request.urlopen(req, timeout=600) as r:
        return io.BytesIO(r.read())


def creators(seasons):
    """Per shooter, how many of his made threes were created and by whom. Play-by-play only."""
    import pandas as pd
    tot, ast = collections.Counter(), collections.Counter()
    feed = collections.defaultdict(collections.Counter)
    names = {}
    for s in seasons:
        try:
            df = pd.read_parquet(grab(PBP % s))
        except Exception as e:
            print('  %s: no play-by-play (%s) - the assisted share will be absent' % (s, e))
            continue
        m3 = df[(df.scoring_play == True)                                   # noqa: E712
                & (df.score_value == 3)
                & (~df.type_text.fillna('').str.contains('Free Throw'))]
        print('  %s: %d made threes' % (s, len(m3)))
        for r in m3.itertuples():
            sc = pid(r.athlete_id_1)
            if not sc:
                continue
            tot[sc] += 1
            names[sc] = nm(r.athlete_name_1) or names.get(sc, '')
            p = pid(r.athlete_id_2)
            if p:
                ast[sc] += 1
                feed[sc][p] += 1
                names[p] = nm(r.athlete_name_2) or names.get(p, '')
    return tot, ast, feed, names


def defence(rows):
    """What a club lets the other side do from three.

    The percentage is carried but deliberately NOT ranked: out of sample a club's three-point
    percentage allowed is worth r=-0.016, which is nothing, and a rank beside the other two
    would read as though the three were comparable reads. Attempts allowed is the real one -
    whether they run you off the line - and even that is only 0.21 of a three.
    """
    tm = collections.defaultdict(lambda: collections.defaultdict(float))
    for r in rows:
        m, t = str(r.get('MatchId')), r.get('Team')
        if not m or not t:
            continue
        tm[m][t + '|made'] += fnum(r.get('threes'))
        tm[m][t + '|att'] += fnum(r.get('threesAtt'))
    acc = collections.defaultdict(lambda: collections.defaultdict(list))
    for m, row in tm.items():
        ts = sorted({k.split('|')[0] for k in row})
        if len(ts) != 2:
            continue
        for a, b in ((ts[0], ts[1]), (ts[1], ts[0])):
            acc[a]['made'].append(row.get(b + '|made', 0.0))
            acc[a]['att'].append(row.get(b + '|att', 0.0))
    clubs = {c: {k: statistics.mean(v) for k, v in kv.items()}
             for c, kv in acc.items() if len(kv['made']) >= MIN_DEF_GAMES}
    out = {}
    for key in ('made', 'att'):
        for n, c in enumerate(sorted(clubs, key=lambda x: -clubs[x][key])):
            out.setdefault(c, {})[key] = round(clubs[c][key], 2)
            out[c][key + 'Rank'] = n + 1
    for c in out:
        out[c]['of'] = len(clubs)
        out[c]['pct'] = round(clubs[c]['made'] / clubs[c]['att'], 4) if clubs[c]['att'] else None
    return out


def stability(byp):
    """His own first half-season against his second - measured here rather than hard-coded, so a
    run on a different window reports what that window actually says."""
    def corr(xs, ys):
        if len(xs) < 20:
            return None
        mx, my = statistics.mean(xs), statistics.mean(ys)
        sx = sum((x - mx) ** 2 for x in xs) ** .5
        sy = sum((y - my) ** 2 for y in ys) ** .5
        if not sx or not sy:
            return None
        return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)
    cols = {'att': ([], []), 'made': ([], []), 'pct': ([], [])}
    for i, rs in byp.items():
        if len(rs) < 40:
            continue
        rs = sorted(rs, key=lambda r: str(r.get('Date') or ''))
        h = len(rs) // 2
        a, b = rs[:h], rs[h:]
        aa = sum(fnum(r.get('threesAtt')) for r in a)
        ba = sum(fnum(r.get('threesAtt')) for r in b)
        if aa < 40 or ba < 40:
            continue
        cols['att'][0].append(statistics.mean(fnum(r.get('threesAtt')) for r in a))
        cols['att'][1].append(statistics.mean(fnum(r.get('threesAtt')) for r in b))
        cols['made'][0].append(statistics.mean(fnum(r.get('threes')) for r in a))
        cols['made'][1].append(statistics.mean(fnum(r.get('threes')) for r in b))
        cols['pct'][0].append(sum(fnum(r.get('threes')) for r in a) / aa)
        cols['pct'][1].append(sum(fnum(r.get('threes')) for r in b) / ba)
    out = {}
    for k, (x, y) in cols.items():
        c = corr(x, y)
        if c is not None:
            out[k] = round(c, 3)
    out['n'] = len(cols['att'][0])
    return out


def build(rows, tot, ast, feed, names):
    byp = collections.defaultdict(list)
    for r in rows:
        if fnum(r.get('minutes')) <= 0:
            continue
        i = pid(r.get('PlayerId'))
        if i:
            byp[i].append(r)

    players = []
    for i, rs in byp.items():
        att = statistics.mean(fnum(r.get('threesAtt')) for r in rs)
        if len(rs) < MIN_GAMES or att < MIN_ATT:
            continue
        made = statistics.mean(fnum(r.get('threes')) for r in rs)
        ta = sum(fnum(r.get('threesAtt')) for r in rs)
        tmk = sum(fnum(r.get('threes')) for r in rs)
        last = sorted(rs, key=lambda r: str(r.get('Date') or ''))[-1]
        n3 = tot.get(i, 0)
        sh = (ast.get(i, 0) / n3) if n3 >= MIN_MADE_PBP else None
        cre = []
        if n3 >= MIN_MADE_PBP:
            for cid, c in feed.get(i, collections.Counter()).most_common(MAX_CREATORS):
                if c / n3 < MIN_CREATOR_SHARE:
                    break
                cre.append({'id': cid, 'name': names.get(cid, cid),
                            'n': c, 'share': round(c / n3, 4)})
        players.append({
            'playerId': i, 'name': nm(rs[-1].get('Player')) or ('ESPN #' + i),
            'team': last.get('Team'), 'games': len(rs),
            'minutes': round(statistics.mean(fnum(r.get('minutes')) for r in rs), 1),
            'made': round(made, 2), 'att': round(att, 2),
            'pct': round(tmk / ta, 4) if ta else None,
            'madeOnFile': n3,
            'assistedShare': round(sh, 4) if sh is not None else None,
            'creators': cre})
    players.sort(key=lambda p: -p['made'])

    lg_att = [fnum(r.get('threesAtt')) for r in rows if fnum(r.get('minutes')) > 0]
    lg_made = [fnum(r.get('threes')) for r in rows if fnum(r.get('minutes')) > 0]
    sa = sum(ast.values())
    stv = sum(tot.values())
    return {
        'league': {
            'made': round(statistics.mean(lg_made), 3) if lg_made else None,
            'att': round(statistics.mean(lg_att), 3) if lg_att else None,
            'pct': round(sum(lg_made) / sum(lg_att), 4) if sum(lg_att) else None,
            'assistedShare': round(sa / stv, 4) if stv else None,
            'stability': stability(byp),
            # measured once over 15,048 player-games; see the docstring. The card quotes these
            # rather than re-deriving them on the fly for every player.
            'recent': {'attToAtt': 0.522, 'pctToPct': -0.0275, 'madeToMade': 0.0376,
                       'hotFifthPts': -1.9, 'coldFifthPts': 0.1, 'n': 15048},
            'oppSwing': {'att': 0.21, 'made': 0.17, 'pct': -0.016},
            'creatorOut': {'gain': 0.006, 'control': 0.084, 'downPct': 52,
                           'catchShoot': 0.024, 'selfCreate': -0.014, 'n': 170}},
        'minGames': MIN_GAMES,
        'defence': defence(rows), 'players': players}


def write(out_dir, payload, seasons):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, 'threes.json')
    if not payload['players'] and os.path.exists(path):
        print('  built nothing - keeping what is already in %s' % path)
        return
    payload['updated'] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
    payload['seasons'] = list(seasons)
    payload['source'] = 'JTT gamelogs; assisted share from ESPN play-by-play'
    with open(path, 'w') as f:
        json.dump(payload, f, separators=(',', ':'), allow_nan=False)
    print('  wrote %s (%.0f KB) - %d shooters, %d clubs'
          % (path, os.path.getsize(path) / 1024, len(payload['players']), len(payload['defence'])))


def load(out_dir, seasons):
    rows = []
    for s in seasons:
        p = os.path.join(out_dir, 'gamelogs_%s.json' % s)
        if not os.path.exists(p):
            print('  %s: no gamelogs_%s.json - skipped' % (s, s))
            continue
        d = json.load(open(p))
        print('  %s: %d player-games' % (s, len(d)))
        rows += d
    return rows


def selftest():
    ok = True

    def check(label, cond):
        nonlocal ok
        print(('  ok   ' if cond else '  FAIL ') + label)
        ok = ok and bool(cond)

    check('a NaN never survives as a number', fnum(float('nan')) == 0.0 and fnum('2.5') == 2.5)
    check('and never as a name', nm(float('nan')) == '' and nm(' Ay ') == 'Ay')

    def g(mid, team, player, i, made, att, mins=30, date='2026-01-01'):
        return {'MatchId': mid, 'Team': team, 'Player': player, 'PlayerId': i, 'Date': date,
                'threes': made, 'threesAtt': att, 'minutes': mins}

    rows = []
    for k in range(30):
        d = '2026-01-%02d' % (k % 28 + 1)
        rows += [g('M%d' % k, 'BOS', 'Splash', '1', 4, 10, date=d),
                 g('M%d' % k, 'BOS', 'Bench', '2', 1, 3, date=d),
                 g('M%d' % k, 'NY', 'Them', '9', 12, 35, date=d)]
    tot = {'1': 120, '2': 30}
    ast = {'1': 110, '2': 9}
    feed = {'1': collections.Counter({'7': 60, '8': 30, '9': 4}),
            '2': collections.Counter({'7': 9})}
    names = {'7': 'Maker', '8': 'Second', '9': 'Tiny'}
    out = build(rows, tot, ast, feed, names)
    sp = [p for p in out['players'] if p['name'] == 'Splash'][0]
    check('his volume and rate are per game', sp['made'] == 4 and sp['att'] == 10)
    check('and the percentage is made over attempted, not a mean of per-game rates',
          abs(sp['pct'] - 0.4) < 1e-9)
    # tolerance to match what is WRITTEN: these land in the file rounded to four places, so a
    # 1e-9 comparison fails a correct build.
    check('the assisted share comes off the play-by-play (%.4f)' % sp['assistedShare'],
          abs(sp['assistedShare'] - 110 / 120) < 1e-4)
    check('his creators are listed busiest first',
          [c['name'] for c in sp['creators']] == ['Maker', 'Second'])
    check('and a creator below the floor is dropped',
          all(c['name'] != 'Tiny' for c in sp['creators']))
    check('shares are of HIS made threes', abs(sp['creators'][0]['share'] - 0.5) < 1e-9)

    # A SHOOTER WITH TOO FEW ON FILE GETS NO SHARE rather than a share built on five shots.
    thin = build(rows, {'1': 5}, {'1': 5}, {}, {})
    sp2 = [p for p in thin['players'] if p['name'] == 'Splash'][0]
    check('a man with five made threes on file is given no assisted share',
          sp2['assistedShare'] is None and sp2['creators'] == [])

    # THE DEFENCE. Percentage carried, deliberately NOT ranked.
    d = out['defence']
    check('a club\'s concession is what the OTHER side did (%s)' % d['BOS']['made'],
          d['BOS']['made'] == 12.0 and d['NY']['made'] == 5.0)
    check('attempts allowed too', d['BOS']['att'] == 35.0 and d['NY']['att'] == 13.0)
    check('made and attempts are both ranked',
          'madeRank' in d['BOS'] and 'attRank' in d['BOS'])
    check('but the percentage allowed is NOT ranked, because it is worth nothing (-0.016)',
          'pct' in d['BOS'] and 'pctRank' not in d['BOS'])
    check('the percentage is still carried for display',
          abs(d['BOS']['pct'] - 12.0 / 35.0) < 1e-4)

    # THE STABILITY MUST BE RECOVERED FROM THE DATA, not asserted. Build a league of shooters
    # whose ATTEMPTS are a fixed personal rate and whose MAKES are a coin flip at a common rate,
    # and the split-half has to come back saying exactly that: attempts persist, percentage does
    # not. A build that reported a high stability for percentage here would be measuring
    # something other than what the card claims.
    import random
    random.seed(11)
    lab = []
    # 200 shooters, not 26. At 26 the standard error on a correlation is about 0.20 and the
    # measured percentage stability swung from -0.43 to +0.56 across seeds - a check that would
    # have passed or failed on luck. At 200 it is about 0.07.
    for n in range(200):
        rate = 3 + (n % 9)                       # his own attempt level, stable all year
        for k in range(44):
            att = max(1, rate + random.choice((-1, 0, 1)))
            made = sum(1 for _ in range(att) if random.random() < 0.36)
            # numeric ids: pid() drops anything that is not all digits, which silently emptied
            # the pool the first time this fixture was written
            lab.append(g('L%d' % k, 'BOS', 'Sh%d' % n, str(1000 + n), made, att,
                         date='2026-%02d-%02d' % (k // 28 + 1, k % 28 + 1)))
        lab.append(g('L0', 'NY', 'Opp%d' % n, str(2000 + n), 2, 6))
    stab = build(lab, {}, {}, {}, {})['league']['stability']
    check('split-half is measured over a real pool (n=%s)' % stab.get('n'), stab.get('n', 0) >= 150)
    check('attempts come back stable (%.2f)' % stab.get('att', -9), stab.get('att', 0) > 0.7)
    check('and percentage comes back unstable (%.2f), which is the card\'s whole point'
          % stab.get('pct', -9), abs(stab.get('pct', 1)) < 0.25)
    check('attempts are measurably more stable than percentage',
          stab.get('att', 0) - abs(stab.get('pct', 0)) > 0.3)

    lg = out['league']
    check('the hot-hand figures are carried',
          lg['recent']['pctToPct'] < 0 and lg['recent']['attToAtt'] > 0.4)
    check('and so is the creator-absence null, so the card can state it',
          abs(lg['creatorOut']['gain']) < 0.05 and lg['creatorOut']['control'] > lg['creatorOut']['gain'])

    check('the payload is valid JSON with no NaN in it', 'NaN' not in json.dumps(out))
    one = [g('X0', 'BOS', 'One', '5', 3, 8), g('X0', 'NY', 'Them', '9', 3, 8)]
    check('a man with one game is not given a profile',
          not [p for p in build(one, {}, {}, {}, {})['players'] if p['name'] == 'One'])

    print('SELFTEST', 'PASSED' if ok else 'FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='nba/data')
    ap.add_argument('--seasons', default=None)
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    if a.seasons:
        seasons = [s for s in a.seasons.split(',') if s]
    else:
        today = datetime.date.today()
        cur = today.year + 1 if today.month >= 9 else today.year
        seasons = [str(cur - 1), str(cur)]
    print('threes: seasons %s' % ','.join(seasons))
    rows = load(a.out, seasons)
    if not rows:
        raise SystemExit('  no gamelogs to read - nothing built')
    tot, ast, feed, names = creators(seasons)
    write(a.out, build(rows, tot, ast, feed, names), seasons)
