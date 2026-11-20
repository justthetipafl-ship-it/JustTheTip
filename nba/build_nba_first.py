#!/usr/bin/env python3
"""First basket: who wins the tip, which team scores first, and who takes that shot.

    python nba/build_nba_first.py --out nba/data [--seasons 2026,2027]
    python nba/build_nba_first.py --selftest          # no network

WHY
First basket is priced on every game and the page had nothing behind it. It is also the market
where a single headline number - "he has scored first in 4 of 15" - is at its least reliable,
because one player gets at most one chance a night and most nights it goes to somebody else.

A first basket is three things in a row, and each can be looked at on its own:

    the tip        who jumps for each team, and how often each wins it
    the team       how often the team that wins the tip goes on to score the first field goal
    the player     of his team's first baskets in the games he started, how many were his -
                   and, separately, how often he took his team's FIRST SHOT, which is the same
                   question asked with five times the evidence

WHAT IS MEASURED, every run, on the window it is given - nothing below is a constant:

  tipToFirst   how often the tip winner scores the first field goal. Above a half, and well
               short of certain.
  stability    first half of a player's starts against the second: his share of his team's
               first BASKETS, and his share of its first SHOTS. The shot share carries much
               better, because a make needs the shot to drop and an attempt does not - which is
               why the page shows both and leans on the second.
  jumper       the same split for a jumper's tip-win rate.
  honest       players grouped by what their record said, against what then happened.

WHAT IS WRITTEN
nba/data/first.json

    {"league":{"tipToFirst","games","stability":{..},"honest":[..],"starterShare"},
     "jumpers":{"<id>":{"name","team","jumps","won","rate"}},
     "teams":{"GS":{"games","tipWon","scoredFirst","jumper"}},
     "players":[{"playerId","name","team","starts","gameFirst","teamFirst","firstShot",
                 "teamFirstRate","firstShotRate"}]}

"First basket" here is the first made FIELD GOAL, which is how the books settle it; a free
throw that comes first does not count.
"""
import argparse
import collections
import datetime
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_nba_stints as ST          # noqa: E402  - its parquet reader and id helper

MIN_STARTS = 8           # starts before a player gets a row
MIN_JUMPS = 5            # jumps before a jumper's rate is written
MIN_SPLIT = 30           # starts (or jumps) before a player is used in a split-half
BANDS = [(0.0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.45), (0.45, 1.01)]


def corr(xs, ys):
    if len(xs) < 20:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx = sum((x - mx) ** 2 for x in xs) ** .5
    sy = sum((y - my) ** 2 for y in ys) ** .5
    if not sx or not sy:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def r3(v):
    return None if v is None else round(v, 3)


def nm(v):
    if v is None:
        return ''
    try:
        if v != v:
            return ''
    except Exception:
        return ''
    t = str(v).strip()
    return '' if t.lower() in ('nan', 'none') else t


def is_fg(type_text, text):
    """A field-goal attempt: a make or a miss that is not a free throw."""
    tt = (type_text or '').lower()
    tx = (text or '')
    if tt.startswith('free throw') or 'rebound' in tt or 'turnover' in tt or 'jumpball' in tt.replace(' ', ''):
        return False
    return ' makes ' in tx or ' misses ' in tx or 'blocks' in tx


def game_record(plays, starters):
    """One game -> what happened first.

    plays     [{'type','text','a1','a2','a3','team','scoring'}] in order
    starters  {player id: team abbr} for the ten who started
    Returns None when the opening tip or the first basket cannot be read.
    """
    tip = None
    first_fg = None
    first_shot = {}
    for p in plays:
        tt = (p['type'] or '').lower().replace(' ', '')
        if tip is None and tt == 'jumpball' and p.get('a1') and p.get('a2'):
            # the feed puts the winning team's id on the row; who "gains possession" is a3
            tip = {'a': p['a1'], 'b': p['a2'], 'team': p.get('team'), 'gainer': p.get('a3')}
            continue
        if not is_fg(p['type'], p['text']):
            continue
        tm = p.get('team')
        if tm and tm not in first_shot and p.get('a1'):
            first_shot[tm] = p['a1']
        if first_fg is None and p.get('scoring') and p.get('a1'):
            first_fg = {'player': p['a1'], 'team': tm}
        if first_fg is not None and len(first_shot) >= 2:
            break
    if first_fg is None:
        return None
    team_first = {}
    seen = set()
    for p in plays:
        if is_fg(p['type'], p['text']) and p.get('scoring') and p.get('a1') and p.get('team') not in seen:
            seen.add(p.get('team'))
            team_first[p.get('team')] = p['a1']
            if len(seen) >= 2:
                break
    return {'tip': tip, 'first': first_fg, 'firstShot': first_shot, 'teamFirst': team_first,
            'starters': dict(starters)}


def tally(games):
    """Game records (in date order) -> league, jumpers, teams, players."""
    pl = collections.defaultdict(lambda: {'starts': 0, 'gameFirst': 0, 'teamFirst': 0, 'firstShot': 0,
                                          'seq': [], 'team': '', 'name': ''})
    jm = collections.defaultdict(lambda: {'jumps': 0, 'won': 0, 'seq': [], 'team': '', 'name': ''})
    tm = collections.defaultdict(lambda: {'games': 0, 'tipWon': 0, 'scoredFirst': 0,
                                          'jumpers': collections.Counter()})
    tip_games = tip_first = 0
    for g in games:
        first_team = g['first']['team']
        for pid_, team in g['starters'].items():
            p = pl[pid_]
            p['starts'] += 1
            p['team'] = team
            p['name'] = g['names'].get(pid_) or p['name']
            gf = 1 if g['first']['player'] == pid_ else 0
            tf = 1 if g['teamFirst'].get(team) == pid_ else 0
            fs = 1 if g['firstShot'].get(team) == pid_ else 0
            p['gameFirst'] += gf
            p['teamFirst'] += tf
            p['firstShot'] += fs
            p['seq'].append((tf, fs))
        for team in set(g['starters'].values()):
            t = tm[team]
            t['games'] += 1
            t['scoredFirst'] += 1 if first_team == team else 0
        tip = g.get('tip')
        if tip and tip.get('team'):
            tip_games += 1
            tip_first += 1 if first_team == tip['team'] else 0
            tm[tip['team']]['tipWon'] += 1
            for who in (tip['a'], tip['b']):
                side = g['starters'].get(who) or g['teams'].get(who)
                if not side:
                    continue
                j = jm[who]
                j['jumps'] += 1
                won = 1 if side == tip['team'] else 0
                j['won'] += won
                j['seq'].append(won)
                j['team'] = side
                j['name'] = g['names'].get(who) or j['name']
                tm[side]['jumpers'][who] += 1

    def split(seqs, pick):
        a, b = [], []
        for s in seqs:
            if len(s) < MIN_SPLIT:
                continue
            h = len(s) // 2
            a.append(statistics.mean(pick(x) for x in s[:h]))
            b.append(statistics.mean(pick(x) for x in s[h:]))
        return r3(corr(a, b)), len(a)

    seqs = [p['seq'] for p in pl.values()]
    rb, nb = split(seqs, lambda x: x[0])
    rs, ns = split(seqs, lambda x: x[1])
    rj, nj = split([j['seq'] for j in jm.values()], lambda x: x)

    # IS A RECORD HONEST? His share of his team's first baskets over the first half of his
    # starts, against what happened in the second half.
    obs = []
    for s in seqs:
        if len(s) < MIN_SPLIT:
            continue
        h = len(s) // 2
        obs.append((statistics.mean(x[0] for x in s[:h]), statistics.mean(x[0] for x in s[h:]),
                    statistics.mean(x[1] for x in s[:h])))
    honest = []
    for lo, hi in BANDS:
        sub = [o for o in obs if lo <= o[0] < hi]
        if len(sub) >= 8:
            honest.append({'lo': lo, 'hi': min(hi, 1.0), 'said': r3(statistics.mean(o[0] for o in sub)),
                           'then': r3(statistics.mean(o[1] for o in sub)), 'n': len(sub)})
    # ... and the same players' later first-basket share against their earlier first-SHOT share
    shot_to_basket = r3(corr([o[2] for o in obs], [o[1] for o in obs]))

    league = {'games': len(games), 'tipGames': tip_games,
              'tipToFirst': r3(tip_first / tip_games) if tip_games else None,
              'stability': {'basket': rb, 'shot': rs, 'n': nb, 'jumper': rj, 'nJumper': nj,
                            'shotToBasket': shot_to_basket},
              'honest': honest, 'starterShare': 0.2}
    jumpers = {i: {'name': j['name'], 'team': j['team'], 'jumps': j['jumps'], 'won': j['won'],
                   'rate': round(j['won'] / j['jumps'], 3)}
               for i, j in jm.items() if j['jumps'] >= MIN_JUMPS and j['name']}
    teams = {}
    for t, v in tm.items():
        if not t or v['games'] < 3:
            continue
        top = v['jumpers'].most_common(1)
        teams[t] = {'games': v['games'], 'tipWon': v['tipWon'], 'scoredFirst': v['scoredFirst'],
                    'jumper': top[0][0] if top else None}
    players = []
    for i, p in pl.items():
        if p['starts'] < MIN_STARTS or not p['name']:
            continue
        players.append({'playerId': i, 'name': p['name'], 'team': p['team'], 'starts': p['starts'],
                        'gameFirst': p['gameFirst'], 'teamFirst': p['teamFirst'], 'firstShot': p['firstShot'],
                        'teamFirstRate': round(p['teamFirst'] / p['starts'], 3),
                        'firstShotRate': round(p['firstShot'] / p['starts'], 3)})
    players.sort(key=lambda r: -r['firstShotRate'])
    return {'league': league, 'jumpers': jumpers, 'teams': teams, 'players': players}


def harvest(seasons):
    games = []
    for s in seasons:
        pbp = ST._read_parquet(ST.PBP % s)
        box = ST._read_parquet(ST.BOX % s)
        if 'season_type' in pbp.columns:
            pbp = pbp[pbp.season_type == 2]
        pbp = pbp[pbp.period_number == 1]
        if 'did_not_play' in box.columns:
            box = box[box.did_not_play != True]            # noqa: E712
        n0 = len(games)
        for gid, g in pbp.groupby('game_id'):
            g = g.sort_values('game_play_number')
            bx = box[box.game_id == gid]
            st = bx[bx.starter == True]                     # noqa: E712
            if len(st) != 10:
                continue
            starters = {ST._pid(r.athlete_id): str(r.team_abbreviation) for r in st.itertuples()}
            names = {ST._pid(r.athlete_id): nm(r.athlete_display_name) for r in bx.itertuples()}
            sides = {ST._pid(r.athlete_id): str(r.team_abbreviation) for r in bx.itertuples()}
            row0 = g.iloc[0]
            abbr = {ST._pid(row0.home_team_id): str(row0.home_team_abbrev),
                    ST._pid(row0.away_team_id): str(row0.away_team_abbrev)}
            plays = []
            for r in g.head(60).itertuples():
                a1 = ST._pid(r.athlete_id_1)
                # the shooter's own team, from the box score - not the row's team id, which on a
                # jump ball is the winner and on some rebounds is the side in possession
                is_jump = str(r.type_text or '').lower().replace(' ', '') == 'jumpball'
                team = abbr.get(ST._pid(r.team_id)) if is_jump else (sides.get(a1) or abbr.get(ST._pid(r.team_id)))
                plays.append({'type': r.type_text, 'text': r.text, 'a1': a1,
                              'a2': ST._pid(r.athlete_id_2), 'a3': ST._pid(getattr(r, 'athlete_id_3', None)),
                              'team': team, 'scoring': bool(r.scoring_play)})
            rec = game_record(plays, starters)
            if not rec:
                continue
            rec['names'] = names
            rec['teams'] = sides
            rec['date'] = str(row0.game_date)[:10]
            games.append(rec)
        print('  %s: %d games read' % (s, len(games) - n0))
    games.sort(key=lambda g: g['date'])
    return games


def write(out_dir, payload, seasons):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, 'first.json')
    if not payload['players'] and os.path.exists(path):
        print('  built nothing - keeping what is already in %s' % path)
        return
    payload['updated'] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
    payload['seasons'] = list(seasons)
    payload['source'] = 'ESPN play-by-play via sportsdataverse'
    body = json.dumps(payload, separators=(',', ':'), allow_nan=False)
    with open(path, 'w') as f:
        f.write(body)
    lg = payload['league']
    print('  wrote %s (%.0f KB) - %d players, %d jumpers, %d teams, %d games'
          % (path, os.path.getsize(path) / 1024, len(payload['players']), len(payload['jumpers']),
             len(payload['teams']), lg['games']))
    print('  tip winner scores first: %s over %d' % (lg['tipToFirst'], lg['tipGames']))
    print('  stability %s' % lg['stability'])
    for b in lg['honest']:
        print('  record said %.2f -> then %.2f (n=%d)' % (b['said'], b['then'], b['n']))


def selftest():
    fails = []

    def ok(name, cond, note=''):
        print(('  ok   ' if cond else '  FAIL ') + name + (('  ' + note) if note else ''))
        if not cond:
            fails.append(name)

    ok('a made jumper is a field goal', is_fg('Jump Shot', 'A makes 20-foot jumper'))
    ok('a miss is a field goal attempt', is_fg('Layup Shot', 'A misses layup'))
    ok('a free throw is not', not is_fg('Free Throw - 1 of 2', 'A makes free throw 1 of 2'))
    ok('nor a rebound, a turnover or the tip', not is_fg('Defensive Rebound', 'A defensive rebound')
       and not is_fg('Lost Ball Turnover', 'A lost ball turnover') and not is_fg('Jumpball', 'A vs. B (C gains possession)'))

    st = {'h1': 'H', 'h2': 'H', 'a1': 'A', 'a2': 'A'}

    def play(t, text, a1, team, scoring=False, a2='', a3=''):
        return {'type': t, 'text': text, 'a1': a1, 'a2': a2, 'a3': a3, 'team': team, 'scoring': scoring}

    # H wins the tip, misses; a free throw by A comes first on the scoreboard and must not count;
    # A's a2 then makes the first field goal; H's h2 makes H's first.
    plays = [play('Jumpball', 'h1 vs. a1 (h2 gains possession)', 'h1', 'H', a2='a1', a3='h2'),
             play('Jump Shot', 'h1 misses jumper', 'h1', 'H'),
             play('Free Throw - 1 of 2', 'a1 makes free throw 1 of 2', 'a1', 'A', scoring=True),
             play('Layup Shot', 'a2 makes layup', 'a2', 'A', scoring=True),
             play('Jump Shot', 'h2 makes jumper', 'h2', 'H', scoring=True)]
    g = game_record(plays, st)
    ok('the tip goes to the team on the jump-ball row', g['tip']['team'] == 'H' and g['tip']['a'] == 'h1' and g['tip']['b'] == 'a1')
    ok('a free throw is not the first basket', g['first'] == {'player': 'a2', 'team': 'A'})
    ok('each team has its own first basket', g['teamFirst'] == {'A': 'a2', 'H': 'h2'})
    ok('and its own first shot, made or not', g['firstShot'] == {'H': 'h1', 'A': 'a2'})
    ok('a quarter with no field goal is no record', game_record(plays[:1], st) is None)

    g['names'] = {'h1': 'Hone', 'h2': 'Htwo', 'a1': 'Aone', 'a2': 'Atwo'}
    g['teams'] = dict(st)
    out = tally([dict(g, date='2027-01-%02d' % d) for d in range(1, 11)])
    P = {p['playerId']: p for p in out['players']}
    ok('starts are counted per starter', P['a2']['starts'] == 10)
    ok('a2 scored the game\'s first basket every night', P['a2']['gameFirst'] == 10 and P['a2']['teamFirstRate'] == 1.0)
    ok('h2 scored his TEAM\'s first without scoring the game\'s', P['h2']['teamFirst'] == 10 and P['h2']['gameFirst'] == 0)
    ok('h1 took his team\'s first shot and never made it', P['h1']['firstShot'] == 10 and P['h1']['teamFirst'] == 0)
    ok('the tip winner did not score first here, and the file says so', out['league']['tipToFirst'] == 0.0)
    ok('jumpers carry their own record', out['jumpers']['h1']['won'] == 10 and out['jumpers']['a1']['rate'] == 0.0)
    ok('the team knows its usual jumper', out['teams']['H']['jumper'] == 'h1' and out['teams']['H']['tipWon'] == 10)
    ok('too few starts measures no stability rather than inventing one',
       out['league']['stability']['basket'] is None and out['league']['honest'] == [])
    json.dumps(out, allow_nan=False)

    print(('\nFAILED: ' + ', '.join(fails)) if fails else '\nall good')
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='nba/data')
    ap.add_argument('--seasons', default=None, help='comma separated; default the last two')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.seasons:
        seasons = [s for s in a.seasons.split(',') if s]
    else:
        try:
            cur = int(json.load(open(os.path.join(a.out, 'meta.json')))['currentSeason'])
        except Exception:
            cur = datetime.date.today().year + (1 if datetime.date.today().month >= 9 else 0)
        seasons = [str(cur - 1), str(cur)]
    print('first basket: seasons %s' % ','.join(seasons))
    games = harvest(seasons)
    if not games:
        print('  no games read - nothing built')
        return 0
    write(a.out, tally(games), seasons)
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
