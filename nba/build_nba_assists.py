#!/usr/bin/env python3
"""Who a passer actually feeds, and what he creates for them.

    python nba/build_nba_assists.py --out nba/data [--seasons 2026,2027]
    python nba/build_nba_assists.py --selftest        # no network

WHY
An assists line is a bet on other people making shots. The box score says a man averages nine a
game and stops there; it cannot say that a fifth of them go to one team-mate, so that when that
team-mate sits the line should move and often does not. That is the read here, and nothing else
in the tool carries it: the passing network, per player, with the share each target takes.

WHAT ESPN GIVES AND WHAT IT DOES NOT
Every made field goal carries the scorer in athlete_id_1 and, where there was one, the assister
in athlete_id_2 - 70,101 of them in the 2026 season, 63.5% of makes, which is the real league
rate. The NAME is not always there when the id is: one athlete in 2026 (id 5107156, 41 assists)
has no name on any row at all, and pandas hands that over as a NaN which json.dump happily writes
as a bare NaN - not JSON, so the page's loader rejects the file and swallows the error. Names are
resolved across every row an id appears on, anything still missing renders as its ESPN id rather
than being dropped, and write() refuses to emit NaN.

It records an assist only on a shot that WENT IN. There is no potential-assist here: a pass that
would have been an assist had the shot dropped leaves no trace at all. That matters, because it
is the single largest source of noise in an assists line - a playmaker has a quiet night when his
shooters miss, not because he passed any worse. Nothing in this file can see that, and the page
says so rather than implying the network explains more than it does.

TWO SEASONS, AND WHO IS STILL THERE
The window is the same two seasons the zones and playtypes files use, because one season this
early is five games a club. That means a network can name a man who has since been traded, which
would be a roster artifact wearing the clothes of analysis - Jokic's list still carries Peyton
Watson, a Cavalier now.

The fix is NOT in this file, and deliberately so. A target's nCur is how many of those assists
came this season, and a zero there does NOT mean he has left: in October it mostly means the
season is five games old. Karl-Anthony Towns sits on zero and is still a Knick. Whether two men
are still team-mates is a question players.json answers exactly - the page joins both playerIds
to it and compares clubs - so that is where the page gets it, and nCur is only ever a volume.

The same goes for a passer's own club. team here is the club he last passed for inside the
window, which for a man yet to assist this season is last season's address; the page badges him
from players.json and falls back to this only for someone off the roster entirely.

WHAT IS WRITTEN
nba/data/assists.json

    {"league": {"threeShare": 0.4247, "assists": 73821},
     "actions": ["putback", ...], "labels": {"jumper": "Catch jumper", ...},
     "defence": {"BOS": {"perGame": 24.1, "rank": 7, "of": 30}},
     "players": [{"playerId":"3112335","name":"Nikola Jokic","team":"DEN",
                  "assists":790,"assistsCur":36,
                  "targets":[{"id":"...","name":"Jamal Murray","n":167,"share":0.21,"nCur":9}, ...],
                  "threeShare":0.37,
                  "acts":{"jumper":0.31,"cut":0.20, ...}}]}

The action keys are the ones build_nba_playtypes.py uses, deliberately: "what he creates" and
"how he scores" then describe shots in the same words, and a reader moving between the two cards
is not learning a second vocabulary. The selftest reads that file and fails if the two drift.
"""
import argparse
import collections
import datetime
import io
import json
import os
import sys
import urllib.request

REL = 'https://github.com/sportsdataverse/sportsdataverse-data/releases/download'
PBP = REL + '/espn_nba_pbp/play_by_play_%s.parquet'

MIN_ASSISTS = 40          # below this a man's network is a handful of games, not a pattern
MAX_TARGETS = 8           # the tail is a long list of ones; the share that matters is at the top
MIN_TARGET_SHARE = 0.03   # and anybody under 3% of his passing is noise in the list
MIN_DEF_GAMES = 20        # a club's concession needs a season behind it to rank

# Same table, same order, same precedence, same labels as build_nba_playtypes.py. Copied rather
# than imported because these are standalone build scripts run independently in CI - and checked
# against that file by the selftest, which reads it, so the two cannot drift silently.
ACTIONS = [
    ('putback',  'Putback',      ('tip', 'putback')),
    ('lob',      'Lob',          ('alley oop',)),
    ('cut',      'Cut',          ('cutting',)),
    ('stepback', 'Step-back',    ('step back',)),
    ('pullup',   'Pull-up',      ('pullup', 'pull up')),
    ('post',     'Post / fade',  ('turnaround', 'fade away', 'fadeaway', 'hook')),
    ('drive',    'Drive',        ('driving',)),
    ('runout',   'Run-out',      ('running',)),
    ('float',    'Floater',      ('floating',)),
    ('rim',      'At the rim',   ('dunk', 'layup', 'finger roll')),
    ('jumper',   'Catch jumper', ('jump shot', 'jumper')),
]
ACTION_KEYS = [a[0] for a in ACTIONS]


def classify(type_text):
    """ESPN's action label -> one of ACTION_KEYS, or '' when it is not a field goal we count."""
    t = (type_text or '').strip().lower()
    if not t or t.startswith('free throw') or t.startswith('no shot'):
        return ''
    for key, _label, needles in ACTIONS:
        for n in needles:
            if n in t:
                return key
    return ''


def pid(v):
    t = str(v).strip()
    if t.endswith('.0'):
        t = t[:-2]
    return t if t.isdigit() else ''


def nm(v):
    """A name, or '' - never a float.

    pandas hands back NaN for a missing athlete_name, and a NaN that reaches json.dump is written
    as a bare NaN, which is not JSON. JSON.parse rejects the whole file, loadTier3 swallows the
    error in its .catch, and the card simply never appears - in the browser as well as in the
    tests. One athlete in the 2026 season (id 5107156, 41 assists) has an id and no name anywhere,
    so this is not hypothetical, and write() passes allow_nan=False so it can never slip through
    again even if some other column starts arriving empty.
    """
    if v is None:
        return ''
    try:
        if v != v:                       # NaN is the only value not equal to itself
            return ''
    except Exception:
        return ''
    t = str(v).strip()
    return '' if t.lower() in ('nan', 'none') else t


def grab(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (JTT build)'})
    with urllib.request.urlopen(req, timeout=600) as r:
        return io.BytesIO(r.read())


def harvest(seasons):
    """-> (assist rows, games per club, assists conceded per club).

    The shooting club comes from team_id against home_team_id, and those two columns are NOT the
    same dtype - team_id arrives as a float (5.0) and home_team_id as an int (5), so comparing
    them as strings matches nothing at all and silently credits every bucket to the away side.
    Both go through Int64 first. The 'home matches' figure printed per season is the guard: it
    sits near 0.5 when the comparison works, and at 0.0 when it does not.
    """
    import pandas as pd
    rows = []
    conceded = collections.Counter()
    club_games = collections.defaultdict(set)
    cur = seasons[-1] if seasons else None
    for s in seasons:
        df = pd.read_parquet(grab(PBP % s))
        tid = df.team_id.astype('Int64').astype(str)
        hid = df.home_team_id.astype('Int64').astype(str)
        at_home = (tid == hid)
        print('  %s: %d plays, %d games, home-side share %.3f'
              % (s, len(df), df.game_id.nunique(), at_home.mean()))
        if not 0.3 < at_home.mean() < 0.7:
            raise SystemExit('  the club comparison is broken for %s (%.3f) - refusing to build '
                             'a file where every bucket is credited to one side' % (s, at_home.mean()))
        shooter = df.home_team_abbrev.where(at_home, df.away_team_abbrev)
        defender = df.away_team_abbrev.where(at_home, df.home_team_abbrev)
        for g in df[['game_id', 'home_team_abbrev', 'away_team_abbrev']].drop_duplicates().itertuples():
            club_games[g.home_team_abbrev].add((s, g.game_id))
            club_games[g.away_team_abbrev].add((s, g.game_id))
        sc = df[(df.scoring_play == True)                              # noqa: E712
                & (~df.type_text.fillna('').str.contains('Free Throw'))
                & (df.athlete_id_2.notna())].copy()
        sc['jttClub'] = shooter[sc.index]
        sc['jttDef'] = defender[sc.index]
        for r in sc.itertuples():
            passer, scorer = pid(r.athlete_id_2), pid(r.athlete_id_1)
            if not passer or not scorer:
                continue
            conceded[r.jttDef] += 1
            rows.append({
                'passer': passer, 'passerName': nm(r.athlete_name_2),
                'scorer': scorer, 'scorerName': nm(r.athlete_name_1),
                'three': 1 if (r.score_value or 0) == 3 else 0,
                'act': classify(r.type_text),
                'team': r.jttClub,
                'cur': 1 if s == cur else 0,
            })
    return rows, club_games, conceded


def build(rows, club_games, conceded):
    """Per-passer networks.

    A club is taken from the man's CURRENT-season rows where he has any, and only from the older
    season when he has none - a modal club across both seasons puts a traded player at his old
    address, which is how Jose Alvarado came out of the 2026 file as a Pelican while assisting
    Knicks. It is still only a fallback for the page, which badges him from players.json.

    Each target carries nCur, how many of those assists came this season. That is a volume and
    nothing more; it is NOT a departure flag, because this early a zero mostly means five games
    have been played. No target is ever dropped for being stale - the page marks a man who has
    left, by roster, rather than quietly removing a fifth of someone's passing.
    """
    # Names, resolved across every row an id appears in - as passer or as scorer. A row can
    # carry the id and not the name, so taking the name off one row leaves gaps that a later row
    # would have filled.
    # nm() again here rather than trusting harvest: a NaN is TRUTHY in Python, so an `if n` guard
    # waves it straight through, and that is exactly how one reached the written file.
    seen = {}
    for r in rows:
        for i, n in ((r['passer'], r['passerName']), (r['scorer'], r['scorerName'])):
            n = nm(n)
            if n and i not in seen:
                seen[i] = n
    # An id with no name anywhere still has to render as something a reader can place, and it must
    # not be dropped: that would take its assists out of the shares and quietly change everyone
    # else's percentages.
    def named(i):
        return seen.get(i) or ('ESPN #' + str(i))

    by = collections.defaultdict(lambda: {'n': 0, 'nCur': 0, 'three': 0,
                                          'tg': collections.Counter(), 'tgCur': collections.Counter(),
                                          'acts': collections.Counter(), 'clubs': collections.Counter(),
                                          'clubsCur': collections.Counter(),
                                          'name': None, 'names': {}})
    for r in rows:
        if r['passer'] == r['scorer']:
            continue                     # a man does not assist himself; ESPN occasionally says so
        p = by[r['passer']]
        p['n'] += 1
        p['three'] += r['three']
        p['tg'][r['scorer']] += 1
        p['names'][r['scorer']] = named(r['scorer'])
        if r['act']:
            p['acts'][r['act']] += 1
        p['name'] = named(r['passer'])
        p['clubs'][r['team']] += 1
        if r['cur']:
            p['nCur'] += 1
            p['tgCur'][r['scorer']] += 1
            p['clubsCur'][r['team']] += 1

    players = []
    for k, v in by.items():
        if v['n'] < MIN_ASSISTS:
            continue
        src = v['clubsCur'] if v['clubsCur'] else v['clubs']
        tgt = []
        for sid, c in v['tg'].most_common(MAX_TARGETS):
            share = c / v['n']
            if share < MIN_TARGET_SHARE:
                break
            tgt.append({'id': sid, 'name': v['names'].get(sid, sid),
                        'n': c, 'share': round(share, 4), 'nCur': v['tgCur'].get(sid, 0)})
        acts = {}
        tot_act = sum(v['acts'].values())
        if tot_act:
            for a in ACTION_KEYS:
                if v['acts'].get(a):
                    acts[a] = round(v['acts'][a] / tot_act, 4)
        players.append({'playerId': k, 'name': v['name'], 'team': src.most_common(1)[0][0],
                        'assists': v['n'], 'assistsCur': v['nCur'], 'targets': tgt,
                        'threeShare': round(v['three'] / v['n'], 4), 'acts': acts})
    players.sort(key=lambda p: -p['assists'])

    # what each club concedes, per game, ranked - most conceded first is the softest to pass on
    dfc = {}
    per = []
    for club, n in conceded.items():
        gms = len(club_games.get(club, ()))
        if gms >= MIN_DEF_GAMES:
            per.append((n / gms, club))
    per.sort(reverse=True)
    for i, (v, club) in enumerate(per):
        dfc[club] = {'perGame': round(v, 2), 'rank': i + 1, 'of': len(per)}

    three = sum(r['three'] for r in rows) / len(rows) if rows else 0
    return {'league': {'threeShare': round(three, 4), 'assists': len(rows)},
            'actions': ACTION_KEYS, 'labels': {a[0]: a[1] for a in ACTIONS},
            'minAssists': MIN_ASSISTS, 'minTargetShare': MIN_TARGET_SHARE,
            'defence': dfc, 'players': players}


def write(out_dir, payload, seasons):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, 'assists.json')
    if not payload['players'] and os.path.exists(path):
        print('  built nothing - keeping what is already in %s' % path)
        return
    payload['updated'] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
    payload['seasons'] = list(seasons)
    payload['source'] = 'ESPN play-by-play via sportsdataverse'
    with open(path, 'w') as f:
        # allow_nan=False: a NaN would be written as a bare NaN, which is not JSON, and the page
        # swallows a parse failure silently. Better to fail here than to ship a file that cannot
        # be read.
        json.dump(payload, f, separators=(',', ':'), allow_nan=False)
    print('  wrote %s (%.0f KB) - %d passers, %d clubs' % (
        path, os.path.getsize(path) / 1024, len(payload['players']), len(payload['defence'])))


def selftest():
    ok = True

    def check(label, cond):
        nonlocal ok
        print(('  ok   ' if cond else '  FAIL ') + label)
        ok = ok and bool(cond)

    # the classifier, pinned against the same precedence playtypes.py relies on
    check('a running pull-up is a pull-up, not a run-out', classify('Running Pullup Jump Shot') == 'pullup')
    check('a running alley oop is a lob', classify('Running Alley Oop Dunk Shot') == 'lob')
    check('a driving floating bank is a drive', classify('Driving Floating Bank Jump Shot') == 'drive')
    check('a tip is a putback', classify('Tip Shot') == 'putback')
    check('a plain jump shot is a catch jumper', classify('Jump Shot') == 'jumper')
    check('a free throw is not a field goal', classify('Free Throw - 1 of 2') == '')
    check('an empty label classifies to nothing', classify('') == '')

    def row(sc, nm, three=0, act='jumper', team='DEN', cur=1, ps='P', psn='Passer'):
        return {'passer': ps, 'passerName': psn, 'scorer': sc, 'scorerName': nm,
                'three': three, 'act': act, 'team': team, 'cur': cur}

    rows = ([row('A', 'Ay', 1, 'jumper'), row('A', 'Ay', 0, 'rim'),
             row('B', 'Bee', 1, 'jumper')] * 20)
    # one target far below the floor, to prove it is dropped rather than listed
    rows += [row('C', 'Cee', 0, 'rim')]
    games = {'DEN': {('2027', i) for i in range(30)}, 'BOS': {('2027', i) for i in range(30)}}
    out = build(rows, games, collections.Counter({'BOS': 600}))
    p = out['players'][0]
    check('the passer is found', p['name'] == 'Passer' and p['assists'] == 61)
    check('his busiest target leads', p['targets'][0]['name'] == 'Ay')
    check('shares are of HIS assists', abs(p['targets'][0]['share'] - 40 / 61) < 1e-3)
    check('a 1-in-61 target is below the floor and dropped',
          all(t['name'] != 'Cee' for t in p['targets']))
    check('three-share is counted', abs(p['threeShare'] - 40 / 61) < 1e-3)
    check('the action mix sums to about one', abs(sum(p['acts'].values()) - 1) < 0.01)
    check('a club\'s concession is per game', out['defence']['BOS']['perGame'] == 20.0)
    check('and ranked', out['defence']['BOS']['rank'] == 1)

    check('the labels travel with the data so the card need not guess them',
          out['labels']['jumper'] == 'Catch jumper' and len(out['actions']) == 11)

    # A TRADED PASSER MUST READ AT HIS NEW CLUB. Taking the club he appears at most often puts him
    # back where he was last season, which is how a network ends up labelled with the wrong badge.
    tr = ([row('A', 'Ay', team='NO', cur=0)] * 50) + ([row('B', 'Bee', team='NY', cur=1)] * 10)
    pt = build(tr, games, collections.Counter())['players'][0]
    check('a traded man reads at this season\'s club, not last season\'s (' + pt['team'] + ')',
          pt['team'] == 'NY')
    check('and both seasons still count toward his volume',
          pt['assists'] == 60 and pt['assistsCur'] == 10)

    # A STALE TARGET MUST STAY VISIBLE. Dropping a man for having no assists yet this season
    # would hide most of everyone's passing in October, when the season is five games old.
    old = [t for t in pt['targets'] if t['name'] == 'Ay']
    check('a target with nothing this season is still listed', len(old) == 1)
    check('and nCur is a volume, not a verdict on whether he has left',
          old and old[0]['nCur'] == 0 and old[0]['n'] == 50)
    check('while a target he has fed this season carries that count',
          [t for t in pt['targets'] if t['name'] == 'Bee'][0]['nCur'] == 10)

    # NO NaN MAY REACH THE FILE. A bare NaN is not JSON; the page rejects the whole file and
    # its loader swallows the error, so the card goes missing with nothing to say why.
    nanny = ([row('A', float('nan'))] * 50) + ([row('B', 'Bee')] * 10)
    nn = build(nanny, games, collections.Counter())
    blob = json.dumps(nn)
    check('a missing name never reaches the payload as NaN', 'NaN' not in blob)
    check('and renders as the ESPN id instead of being dropped, so the shares still add up',
          any(t['name'].startswith('ESPN #') for t in nn['players'][0]['targets'])
          and nn['players'][0]['assists'] == 60)
    check('nm() flattens every empty shape pandas hands over',
          nm(float('nan')) == '' and nm(None) == '' and nm('nan') == ''
          and nm('  Ay  ') == 'Ay')
    # and a name present on ANY row fills the gaps on the rest
    gappy = [row('A', 'Ay')] + ([row('A', float('nan'))] * 59)
    gp = build(gappy, games, collections.Counter())['players'][0]
    check('a name on one row names that man on all of them',
          gp['targets'][0]['name'] == 'Ay')

    thin = [row('A', 'Ay')] * 5
    out2 = build(thin, games, collections.Counter())
    check('a man with five assists on file is not given a network', not out2['players'])

    # A man cannot assist himself. Mixed in with real passing, so the check proves the row is
    # dropped rather than that the whole player vanished.
    selfy = ([row('A', 'Ay')] * 50) + ([row('P', 'Passer')] * 20)
    sp = build(selfy, games, collections.Counter())['players'][0]
    check('a self-assist is dropped, not counted',
          sp['assists'] == 50 and all(t['id'] != 'P' for t in sp['targets']))

    # THE TWO SHOT TAXONOMIES MUST NOT DRIFT. "What he creates" and "How he scores" describe the
    # same shots; if someone retunes one table the reader is silently comparing two vocabularies.
    # This reads the other build script rather than trusting the comment above it.
    sib = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'build_nba_playtypes.py')
    if not os.path.exists(sib):
        check('build_nba_playtypes.py is there to check against', False)
    else:
        import re
        src = open(sib).read()
        m = re.search(r'\nACTIONS = \[(.*?)\n\]', src, re.S)
        if not m:
            check('its action table can be read', False)
        else:
            theirs = re.sub(r'\s+', ' ', m.group(1)).strip()
            mine = re.sub(r'\s+', ' ', repr(ACTIONS)[1:-1]).strip()
            # compare as data, not as text: same keys, labels and needles, in the same order
            ns = {}
            exec('ACTIONS = [' + m.group(1) + '\n]', ns)       # noqa: S102 - our own file
            check('the shot taxonomy matches build_nba_playtypes.py exactly'
                  + ('' if ns['ACTIONS'] == ACTIONS else '\n         theirs: ' + theirs
                     + '\n         mine:   ' + mine),
                  ns['ACTIONS'] == ACTIONS)

    print('SELFTEST', 'PASSED' if ok else 'FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='nba/data')
    ap.add_argument('--seasons', default=None, help='comma separated; default the last two')
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
    print('assists: seasons %s' % ','.join(seasons))
    rows, club_games, conceded = harvest(seasons)
    print('  %d assisted field goals' % len(rows))
    write(a.out, build(rows, club_games, conceded), seasons)
