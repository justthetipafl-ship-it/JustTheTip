#!/usr/bin/env python3
"""Who takes the glass, and what opens up when the man next to him sits.

    python nba/build_nba_rebounds.py --out nba/data [--seasons 2026,2027]
    python nba/build_nba_rebounds.py --selftest        # no network, no files

WHY THIS ONE IS DIFFERENT FROM PLAYMAKING
The assists card had to say that its headline read failed: when a passer's busiest target sits,
his assists do not move (+0.00 across 189 passers), because losing a target costs him buckets and
hands him the ball in equal measure.

Rebounds are not like that, and the reason is mechanical: a rebound is a contested pool, so a
team-mate who is not on the floor is not taking boards off him. Measured the same way, over the
2026 season:

    top rebounding team-mate out   +0.86 rebounds   (median +0.65)
    a rank 6-9 team-mate out       +0.21            - the control
    players who rebound MORE       137 of 188, 73%  - against 46% for assists

and it splits into two things worth separating, because they are different claims:

    +0.51   he simply plays more        (+2.05 minutes on average)
    +0.34   he takes a larger share     (TRB% up 0.72 points, 64% of players)

HOW MUCH FOR A PARTICULAR MAN, not just the top one. Fitted across 3,100 player/team-mate pairs
with eight games either side:

    gain ~= 0.0825 x (the absent man's rebounds per game) - 0.078        r = +0.163

which lands on the bands it was fitted from: a rival averaging 10-12 boards is worth +0.87, one
averaging 2-4 is worth +0.09. The per-pair noise is large - MAE 0.84 against 0.85 for a flat
constant - so this is a nudge, not a certainty, and the page says that rather than printing it
like a projection.

THE OPPONENT is the weaker strand here, the reverse of the assists card. Rebounds conceded per
club, tested out of sample (last season's table against this season's games): r=+0.056 and 0.43
of a rebound between the softest and toughest third, against 0.65 for assists, and the ranking
is less stable season to season (+0.57 against +0.74). Real, but small, and printed as small.

THE OPPONENT BLOCK carries pace as a second read, not decoration: it is worth 0.54 of a rebound
out of sample against the concession's 0.43, and residualised it adds +0.035 on TOP of it, which
is most of the concession's own signal again. The boards-and-share line below it is a breakdown
only - the share adds +0.0008 once the concession is known, because the two correlate at +0.73.
See defence().

NO PLAY-BY-PLAY. Unlike the zones, actions and passing builds this reads the gamelogs already in
the repo. The rebound-rate formula needs only minutes and the two clubs' totals, and nothing the
play-by-play adds - it has no contested/uncontested flag and no rebound location - would change a
number here. So this runs offline in a second rather than pulling 650k rows of parquet.

WHAT IS WRITTEN
nba/data/rebounds.json

    {"league": {"reb": 43.6, "trbPct": 8.7,
                "rival": {"slope": 0.0825, "intercept": -0.078, "top": 0.86,
                          "fromMinutes": 0.51, "fromShare": 0.34, "control": 0.21,
                          "n": 188, "pairs": 3100, "mae": 0.84},
                "oppSwing": 0.43},
     "defence": {"BOS": {"perGame": 44.2, "rank": 7, "of": 30,
                         "pace": 101.4, "paceRank": 12,
                         "boards": 88.1, "oppShare": 0.5017, "misses": 86.3}},
     "players": [{"playerId": "...", "name": "...", "team": "DEN", "games": 71,
                  "minutes": 34.2, "reb": 12.7, "oreb": 2.8, "dreb": 9.9,
                  "trbPct": 19.4, "orebPct": 8.8, "drebPct": 29.1,
                  "rivals": [{"id": "...", "name": "...", "reb": 9.2, "games": 68}, ...]}]}

The page marks which rivals are out tonight from its own injury and lineup data, and adds up the
fitted gain. That join is not done here on purpose: availability changes by the hour and this
file is rebuilt once a day.
"""
import argparse
import collections
import datetime
import json
import os
import statistics
import sys

MIN_GAMES = 10          # below this a man's rates are a handful of nights
MIN_REB = 1.0           # and below this he is not a rebounding read at all
MAX_RIVALS = 6          # the men who actually take boards off him; the tail is noise
MIN_DEF_GAMES = 20      # a club's concession needs a season behind it to rank
# Fitted across 3,100 player/team-mate pairs - see the docstring. Defaults, not magic: a run with
# enough history refits them and writes what it measured.
RIVAL_SLOPE = 0.0825
RIVAL_INTERCEPT = -0.078
MIN_PAIR_GAMES = 8      # games either side of a team-mate's absence before a pair counts
MIN_FIT_PAIRS = 400     # below this the run keeps the fitted defaults rather than refitting
# A RIVAL'S REBOUNDING IS EASED TOWARD THE LEAGUE, by this many games of average evidence.
# Ranking men on raw per-game put Trevon Brazile, 10.5 boards across TWO games, above Aaron
# Gordon as Jokic's biggest rival - and since the gain formula multiplies the rival's figure,
# his absence would have been priced at +0.7 rebounds off a two-game sample. Eased at k=10 he
# reads 4.7 against Gordon's 5.4 and falls below him, which is the right order. The raw figure
# and the game count are both still written, so the page can show what it was eased from.
RIVAL_SHRINK = 10


def pid(v):
    t = str(v).strip()
    if t.endswith('.0'):
        t = t[:-2]
    return t if t else ''


def fnum(v):
    try:
        f = float(v)
        return 0.0 if f != f else f           # NaN -> 0; see nm() in build_nba_assists.py
    except (TypeError, ValueError):
        return 0.0


def nm(v):
    """A name, never a float. Same guard as the assists build, for the same reason: a NaN written
    into JSON is not JSON, the page rejects the file in a .catch, and the card silently vanishes."""
    if v is None:
        return ''
    try:
        if v != v:
            return ''
    except Exception:
        return ''
    t = str(v).strip()
    return '' if t.lower() in ('nan', 'none') else t


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


def shape(rows):
    """-> per-match club totals, who appeared, and each man's rows."""
    club_reb = collections.defaultdict(lambda: collections.defaultdict(float))
    club_min = collections.defaultdict(lambda: collections.defaultdict(float))
    appeared = collections.defaultdict(set)
    byp = collections.defaultdict(list)
    for r in rows:
        m, t = str(r.get('MatchId')), r.get('Team')
        if not m or not t:
            continue
        club_reb[m][t] += fnum(r.get('rebounds'))
        club_min[m][t] += fnum(r.get('minutes'))
        if fnum(r.get('minutes')) <= 0:
            continue                          # a DNP is not an appearance
        i = pid(r.get('PlayerId'))
        if not i:
            continue
        appeared[m].add(i)
        byp[i].append(r)
    return club_reb, club_min, appeared, byp


def rate(r, club_reb, club_min, kind='rebounds'):
    """His share of the boards available while he was out there.

    The standard formula: a player's rebounds, scaled by how much of the game his club played,
    over his minutes times every rebound BOTH clubs took. Minutes alone do not do this - a man
    on a fast team sees more misses, and a man beside a dominant rebounder sees fewer.
    """
    m, t = str(r.get('MatchId')), r.get('Team')
    opp = [x for x in club_reb.get(m, {}) if x != t]
    mp = fnum(r.get('minutes'))
    if len(opp) != 1 or mp <= 0:
        return None
    if kind == 'oreb':
        pool = club_reb[m][t] + club_reb[m][opp[0]]      # all boards, for a comparable share
    else:
        pool = club_reb[m][t] + club_reb[m][opp[0]]
    tmp = club_min.get(m, {}).get(t, 0.0)
    if pool <= 0 or tmp <= 0:
        return None
    return 100.0 * (fnum(r.get(kind)) * (tmp / 5.0)) / (mp * pool)


# How much of the gain is simply the minutes he inherits, by how big the absent man is. This is
# NOT one ratio: for a rival who barely rebounds the whole (tiny) effect is minutes and there is
# no share gain at all, while for a double-figure rebounder about two thirds is minutes and the
# rest is a genuinely larger share of the glass. Applying the top-rival ratio to a bench man
# overstates the share half, which is the more interesting of the two claims and so the one that
# should not be inflated. Bands are upper bounds in rebounds per game.
BANDS = (3, 6, 9, 999)


def fit_rivals(byp, appeared, club_of, rpg):
    """Refit gain-against-the-absent-man's-rebounds on this window, or keep the defaults.

    Also measures, per band, what fraction of the gain is explained by the extra minutes alone -
    the player's own per-minute rate times the minutes he gains.
    """
    mates = collections.defaultdict(list)
    for i, c in club_of.items():
        mates[c].append(i)
    xs, ys = [], []
    band = collections.defaultdict(lambda: {'tot': [], 'min': []})
    for i, rs in byp.items():
        if len(rs) < 30 or rpg.get(i, 0) < 3:
            continue
        mine = {str(r.get('MatchId')): r for r in rs}
        for j in mates.get(club_of[i], ()):
            if j == i or len(byp.get(j, ())) < MIN_GAMES:
                continue
            on = [r for m, r in mine.items() if j in appeared[m]]
            off = [r for m, r in mine.items() if j not in appeared[m]]
            if len(on) < MIN_PAIR_GAMES or len(off) < MIN_PAIR_GAMES:
                continue
            r_on = statistics.mean(fnum(r.get('rebounds')) for r in on)
            r_off = statistics.mean(fnum(r.get('rebounds')) for r in off)
            m_on = statistics.mean(fnum(r.get('minutes')) for r in on)
            m_off = statistics.mean(fnum(r.get('minutes')) for r in off)
            xs.append(rpg.get(j, 0.0))
            ys.append(r_off - r_on)
            if m_on > 0:
                b = next(k for k in BANDS if rpg.get(j, 0.0) < k)
                band[b]['tot'].append(r_off - r_on)
                band[b]['min'].append((r_on / m_on) * (m_off - m_on))
    bands = []
    for k in BANDS:
        d = band.get(k)
        if not d or len(d['tot']) < 50:
            continue
        tot, mn = statistics.mean(d['tot']), statistics.mean(d['min'])
        # clamped: a band can measure above 1 (all of it is minutes, plus noise), and a negative
        # or tiny denominator would otherwise produce a nonsense fraction
        frac = 1.0 if tot <= 0.05 else max(0.4, min(1.0, mn / tot))
        bands.append({'upTo': k, 'minuteFrac': round(frac, 3), 'n': len(d['tot']),
                      'gain': round(tot, 3)})
    if len(xs) < MIN_FIT_PAIRS:
        return {'slope': RIVAL_SLOPE, 'intercept': RIVAL_INTERCEPT,
                'pairs': len(xs), 'fitted': False, 'bands': bands}
    mx, my = statistics.mean(xs), statistics.mean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 0:
        return {'slope': RIVAL_SLOPE, 'intercept': RIVAL_INTERCEPT,
                'pairs': len(xs), 'fitted': False, 'bands': bands}
    k = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    b = my - k * mx
    mae = statistics.mean(abs(y - (k * x + b)) for x, y in zip(xs, ys))
    return {'slope': round(k, 4), 'intercept': round(b, 4), 'pairs': len(xs),
            'mae': round(mae, 3), 'fitted': True, 'bands': bands}


def defence(rows):
    """What each club concedes on the glass, and the two things that drive it.

    A concession figure on its own says nothing about WHY, and the two reasons pull in different
    directions: a club can give up boards because its games are fast and full of misses, or
    because it loses the ones that come up. Both are measured and tested out of sample against
    this season's games, and they do NOT carry the same weight:

        rebounds conceded   r=+0.056, 0.43 of a rebound between the softest and toughest third
        pace                r=+0.052, 0.54 - and it adds r=+0.035 ON TOP of the concession,
                            which is most of the concession's own signal again, so it is a
                            genuinely separate read rather than the same one restated
        the opponent's
        share of the glass  r=+0.041, 0.50 - but it adds only +0.0008 on top, because it
                            correlates +0.73 with the concession. Explanatory, not extra.

    So the card prints pace as a second strand and the share as a breakdown, and says which is
    which. boards x oppShare = perGame exactly, by construction, so the breakdown always adds up.
    """
    tm = collections.defaultdict(lambda: collections.defaultdict(float))
    for r in rows:
        m, t = str(r.get('MatchId')), r.get('Team')
        if not m or not t:
            continue
        for k in ('fga', 'fgm', 'fta', 'oreb', 'rebounds', 'turnovers'):
            tm[m][t + '|' + k] += fnum(r.get(k))
    acc = collections.defaultdict(lambda: collections.defaultdict(list))
    for m, row in tm.items():
        ts = sorted({k.split('|')[0] for k in row})
        if len(ts) != 2:
            continue
        gv = lambda t, k: row.get(t + '|' + k, 0.0)                      # noqa: E731
        for a, b in ((ts[0], ts[1]), (ts[1], ts[0])):
            # possessions, the standard estimate, averaged over the two sides
            pa = gv(a, 'fga') - gv(a, 'oreb') + gv(a, 'turnovers') + 0.44 * gv(a, 'fta')
            pb = gv(b, 'fga') - gv(b, 'oreb') + gv(b, 'turnovers') + 0.44 * gv(b, 'fta')
            boards = gv(a, 'rebounds') + gv(b, 'rebounds')
            acc[a]['perGame'].append(gv(b, 'rebounds'))
            acc[a]['pace'].append((pa + pb) / 2.0)
            acc[a]['boards'].append(boards)
            acc[a]['misses'].append((gv(a, 'fga') - gv(a, 'fgm')) + (gv(b, 'fga') - gv(b, 'fgm')))
    clubs = {c: {k: statistics.mean(v) for k, v in kv.items()}
             for c, kv in acc.items() if len(kv['perGame']) >= MIN_DEF_GAMES}
    out = {}
    for key, rev in (('perGame', True), ('pace', True)):
        for n, c in enumerate(sorted(clubs, key=lambda x: -clubs[x][key] if rev else clubs[x][key])):
            out.setdefault(c, {})[key] = round(clubs[c][key], 2)
            out[c][key + 'Rank'] = n + 1
    for c in out:
        out[c]['of'] = len(clubs)
        out[c]['boards'] = round(clubs[c]['boards'], 1)
        out[c]['misses'] = round(clubs[c]['misses'], 1)
        # derived so the breakdown is exact: boards x oppShare == perGame
        out[c]['oppShare'] = round(clubs[c]['perGame'] / clubs[c]['boards'], 4) \
            if clubs[c]['boards'] else None
        out[c]['rank'] = out[c]['perGameRank']          # the headline rank, named as before
    return out


def build(rows):
    club_reb, club_min, appeared, byp = shape(rows)
    names, club_of, raw = {}, {}, {}
    for i, rs in byp.items():
        names[i] = nm(rs[-1].get('Player')) or ('ESPN #' + i)
        c = collections.Counter(r.get('Team') for r in rs if r.get('Team'))
        # his CURRENT club: the one he last played for, not the one he played for most. A man
        # traded in January is at his new club, exactly as in the assists build.
        last = sorted(rs, key=lambda r: str(r.get('Date') or ''))[-1]
        club_of[i] = last.get('Team') or (c.most_common(1)[0][0] if c else None)
        raw[i] = statistics.mean(fnum(r.get('rebounds')) for r in rs)

    # the prior a short record is eased toward: what a man who plays actually averages
    settled = [raw[i] for i in byp if len(byp[i]) >= MIN_GAMES]
    prior = statistics.median(settled) if settled else (
        statistics.median(list(raw.values())) if raw else 0.0)
    rpg = {i: (raw[i] * len(byp[i]) + RIVAL_SHRINK * prior) / (len(byp[i]) + RIVAL_SHRINK)
           for i in byp}

    mates = collections.defaultdict(list)
    for i, c in club_of.items():
        if c:
            mates[c].append(i)
    for c in mates:
        mates[c].sort(key=lambda i: -rpg.get(i, 0))

    players = []
    for i, rs in byp.items():
        if len(rs) < MIN_GAMES or raw[i] < MIN_REB:
            continue
        tr = [x for x in (rate(r, club_reb, club_min) for r in rs) if x is not None]
        orb = [x for x in (rate(r, club_reb, club_min, 'oreb') for r in rs) if x is not None]
        drb = [x for x in (rate(r, club_reb, club_min, 'dreb') for r in rs) if x is not None]
        rivals = []
        for j in mates.get(club_of[i], ())[:MAX_RIVALS + 1]:
            if j == i or len(rivals) >= MAX_RIVALS:
                continue
            if rpg.get(j, 0) < MIN_REB:
                continue
            rivals.append({'id': j, 'name': names.get(j, j),
                           # 'reb' is what the gain is computed from, so it is the eased figure;
                           # 'rebRaw' and 'games' are what it was eased from, for the page to show
                           'reb': round(rpg[j], 2), 'rebRaw': round(raw[j], 2),
                           'games': len(byp[j])})
        players.append({
            'playerId': i, 'name': names[i], 'team': club_of[i], 'games': len(rs),
            'minutes': round(statistics.mean(fnum(r.get('minutes')) for r in rs), 1),
            'reb': round(raw[i], 2),
            'oreb': round(statistics.mean(fnum(r.get('oreb')) for r in rs), 2),
            'dreb': round(statistics.mean(fnum(r.get('dreb')) for r in rs), 2),
            'trbPct': round(statistics.mean(tr), 2) if tr else None,
            'orebPct': round(statistics.mean(orb), 2) if orb else None,
            'drebPct': round(statistics.mean(drb), 2) if drb else None,
            'rivals': rivals})
    players.sort(key=lambda p: -p['reb'])


    dfc = defence(rows)

    fit = fit_rivals(byp, appeared, club_of, rpg)
    allr = [x for x in (rate(r, club_reb, club_min) for r in rows
                        if fnum(r.get('minutes')) > 0) if x is not None]
    # rebounds a club takes in a game, averaged over every club-game in the window
    sides = [v for t in club_reb.values() for v in t.values()]
    lg_reb = statistics.mean(sides) if sides else 0
    return {
        'league': {
            'reb': round(lg_reb, 2),
            'trbPct': round(statistics.median(allr), 2) if allr else None,
            # the measurements the card quotes; see the docstring for how each was taken
            'rival': dict(fit, top=0.86, fromMinutes=0.51, fromShare=0.34,
                          control=0.21, n=188, extraMinutes=2.05, upPct=73),
            # measured out of sample; paceSwing is worth more than the concession itself and
            # is the only contributor that adds anything on top of it
            'oppSwing': 0.43, 'oppStability': 0.57,
            'paceSwing': 0.54, 'paceAdds': 0.035, 'shareAdds': 0.0008},
        'minGames': MIN_GAMES, 'rivalShrink': RIVAL_SHRINK, 'rivalPrior': round(prior, 2),
        'defence': dfc, 'players': players}


def write(out_dir, payload, seasons):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, 'rebounds.json')
    if not payload['players'] and os.path.exists(path):
        print('  built nothing - keeping what is already in %s' % path)
        return
    payload['updated'] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
    payload['seasons'] = list(seasons)
    payload['source'] = 'JTT gamelogs (box score); rebound rate is the standard TRB% formula'
    with open(path, 'w') as f:
        json.dump(payload, f, separators=(',', ':'), allow_nan=False)
    print('  wrote %s (%.0f KB) - %d players, %d clubs'
          % (path, os.path.getsize(path) / 1024, len(payload['players']), len(payload['defence'])))


def selftest():
    ok = True

    def check(label, cond):
        nonlocal ok
        print(('  ok   ' if cond else '  FAIL ') + label)
        ok = ok and bool(cond)

    check('a NaN never survives as a number', fnum(float('nan')) == 0.0 and fnum(None) == 0.0
          and fnum('7.5') == 7.5)
    check('and never as a name', nm(float('nan')) == '' and nm('nan') == '' and nm(' Ay ') == 'Ay')
    check('ids lose the float tail pandas leaves on them', pid('3945274.0') == '3945274')

    def g(mid, team, player, i, reb, mins, oreb=0, dreb=None, date='2026-01-01'):
        return {'MatchId': mid, 'Team': team, 'Player': player, 'PlayerId': i, 'Date': date,
                'rebounds': reb, 'minutes': mins, 'oreb': oreb,
                'dreb': reb - oreb if dreb is None else dreb}

    # THE RATE. Two men, same club, same game: one plays half the minutes and takes half as many
    # boards, so their SHARE of the glass must come out identical. A per-minute figure would
    # already do that; what this also has to survive is the other club's rebounds counting.
    rows = []
    for k in range(22):          # over MIN_DEF_GAMES, so the concession table is built
        m = 'M%d' % k
        rows += [g(m, 'DEN', 'Big', '1', 10, 30), g(m, 'DEN', 'Half', '2', 5, 15),
                 g(m, 'DEN', 'Rest', '3', 5, 195),
                 g(m, 'BOS', 'Them', '9', 20, 240)]
    cr, cm, ap, byp = shape(rows)
    r1 = rate(rows[0], cr, cm)
    r2 = rate(rows[1], cr, cm)
    check('two men at the same share of the glass rate the same (%.2f v %.2f)' % (r1, r2),
          abs(r1 - r2) < 1e-6)
    check('and the rate counts BOTH clubs\' boards, so it is a share not a count',
          abs(r1 - 100.0 * (10 * (240 / 5.0)) / (30 * 40)) < 1e-6)
    check('a man who did not play has no rate', rate(g('M0', 'DEN', 'Dnp', '4', 0, 0), cr, cm) is None)

    out = build(rows)
    big = [p for p in out['players'] if p['name'] == 'Big'][0]
    check('his per-game numbers are per game, not totals', big['reb'] == 10 and big['games'] == 22)
    check('offensive and defensive split out', big['oreb'] == 0 and big['dreb'] == 10)
    check('the concession table is what the OTHER club took',
          out['defence']['DEN']['perGame'] == 20.0 and out['defence']['BOS']['perGame'] == 20.0)
    check('ranks run 1..n with no gaps',
          sorted(x['rank'] for x in out['defence'].values()) == [1, 2])
    check('his team-mates are listed as rivals, he is not his own',
          [r['name'] for r in big['rivals']] and all(r['id'] != '1' for r in big['rivals']))
    check('and they are ordered by who actually takes boards',
          all(big['rivals'][n - 1]['reb'] >= big['rivals'][n]['reb']
              for n in range(1, len(big['rivals']))))

    # A TRADED MAN READS AT HIS NEW CLUB, by his last game and not by where he played most.
    tr = []
    for k in range(20):
        tr += [g('T%d' % k, 'NO', 'Mover', '5', 8, 30, date='2026-01-%02d' % (k + 1)),
               g('T%d' % k, 'BOS', 'Them', '9', 8, 30, date='2026-01-%02d' % (k + 1))]
    for k in range(4):
        tr += [g('U%d' % k, 'NY', 'Mover', '5', 8, 30, date='2026-03-%02d' % (k + 1)),
               g('U%d' % k, 'BOS', 'Them', '9', 8, 30, date='2026-03-%02d' % (k + 1))]
    mv = [p for p in build(tr)['players'] if p['name'] == 'Mover'][0]
    check('a traded man reads at the club he last played for (%s)' % mv['team'], mv['team'] == 'NY')

    # THE FIT falls back rather than inventing a slope from four pairs.
    check('a thin window keeps the measured defaults rather than refitting',
          out['league']['rival']['fitted'] is False
          and out['league']['rival']['slope'] == RIVAL_SLOPE)
    check('and the gain for a 10-rebound rival lands where it was measured (%+.2f)'
          % (RIVAL_SLOPE * 10 + RIVAL_INTERCEPT),
          0.6 < RIVAL_SLOPE * 10 + RIVAL_INTERCEPT < 0.95)
    check('while a 3-rebound bench man is worth almost nothing (%+.2f)'
          % (RIVAL_SLOPE * 3 + RIVAL_INTERCEPT),
          0 < RIVAL_SLOPE * 3 + RIVAL_INTERCEPT < 0.25)

    # A TWO-GAME RIVAL MUST NOT OUTRANK A REGULAR. This is the Brazile case, built small: a man
    # with two huge games against a steady rotation player. Ranking on raw per-game put the
    # two-game man first and priced his absence off that figure.
    # A REALISTIC ROSTER, because the easing is toward the league's own median and a fixture of
    # three men sets that median wherever the fixture likes. On the real file it lands at 3.3.
    bench = [('B%d' % n, 'Bench%d' % n, 2.0 + n * 0.4) for n in range(9)]
    sh = []
    for k in range(40):
        d8 = '2026-02-%02d' % (k % 28 + 1)
        sh += [g('S%d' % k, 'DEN', 'Star', '1', 12, 34, date=d8),
               g('S%d' % k, 'DEN', 'Steady', '2', 6, 30, date=d8)]
        for bid, bn, br in bench:
            sh.append(g('S%d' % k, 'DEN', bn, bid, br, 16, date=d8))
        for n in range(9):
            sh.append(g('S%d' % k, 'BOS', 'Them%d' % n, 'T%d' % n, 3.0, 26, date=d8))
    for k in range(2):
        d8 = '2026-03-0%d' % (k + 1)
        sh += [g('F%d' % k, 'DEN', 'Flash', '3', 11, 30, date=d8),
               g('F%d' % k, 'DEN', 'Star', '1', 12, 34, date=d8)]
        for n in range(9):
            sh.append(g('F%d' % k, 'BOS', 'Them%d' % n, 'T%d' % n, 3.0, 26, date=d8))
    sb = build(sh)
    star = [p for p in sb['players'] if p['name'] == 'Star'][0]
    order = [r['name'] for r in star['rivals']]
    flash = [r for r in star['rivals'] if r['name'] == 'Flash']
    check('a two-game 11-rebound man does not outrank a 40-game 6-rebound man (%s)'
          % ' > '.join(order), order and order[0] == 'Steady')
    check('his raw figure and his game count are both still written, so the easing is visible',
          flash and flash[0]['rebRaw'] == 11.0 and flash[0]['games'] == 2
          and flash[0]['reb'] < flash[0]['rebRaw'])
    check('and a settled man is barely moved by the easing',
          all(abs(r['reb'] - r['rebRaw']) < 1.0
              for r in star['rivals'] if r['games'] >= 40))

    # THE BREAKDOWN MUST ADD UP. boards x oppShare has to equal the headline exactly, or the
    # card prints a decomposition of a number it does not decompose.
    for c, v in out['defence'].items():
        check('%s: boards x the share they allow is the concession (%.1f x %.3f = %.2f v %.2f)'
              % (c, v['boards'], v['oppShare'], v['boards'] * v['oppShare'], v['perGame']),
              abs(v['boards'] * v['oppShare'] - v['perGame']) < 0.05)
    check('pace is measured and ranked separately from the concession',
          all('pace' in v and 'paceRank' in v for v in out['defence'].values()))
    check('and both rankings run 1..n',
          sorted(v['rank'] for v in out['defence'].values()) == list(range(1, len(out['defence']) + 1))
          and sorted(v['paceRank'] for v in out['defence'].values())
              == list(range(1, len(out['defence']) + 1)))

    check('the payload is valid JSON with no NaN in it', 'NaN' not in json.dumps(out))

    thin = [g('X0', 'DEN', 'One', '7', 9, 30), g('X0', 'BOS', 'Them', '9', 9, 30)]
    check('a man with one game on file is not given a profile',
          not [p for p in build(thin)['players'] if p['name'] == 'One'])

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
    print('rebounds: seasons %s' % ','.join(seasons))
    rows = load(a.out, seasons)
    if not rows:
        raise SystemExit('  no gamelogs to read - nothing built')
    print('  %d player-games' % len(rows))
    write(a.out, build(rows), seasons)
