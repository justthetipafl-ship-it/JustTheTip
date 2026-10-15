#!/usr/bin/env python3
"""Volumes - a line priced off a conversion run instead of the opportunity behind it.

    python3 scripts/build_volumes.py nbl            # writes nbl/data/volumes.json
    python3 scripts/build_volumes.py nbl --print    # show what it found, write nothing
    python3 scripts/build_volumes.py --selftest

THE BET
A player's opportunity is far steadier than what he does with it. Shots, targets, carries and
minutes barely move week to week; the percentage he converts them at swings wildly. The market
prices the OUTPUT, so three hot games pull a line up above anything the opportunity behind it
supports. Fade it. The opportunity is still there next week - the percentage is not.

WHY ONLY THE SELL SIDE SHIPS
The mirror case - cold run, volume held, so the over is cheap - is the one that reads better and
loses money. Measured against three weeks of real posted lines it returned -8.9% in the NFL at
every threshold tried, and tightening the logic made it worse, not better: demanding the volume be
flat or GROWING and the conversion genuinely cold, which is the purest statement of the thesis,
was the worst cut of all at -11.7%. There is a reason, and it is physical. Conversion has a
ceiling and no floor. Nobody sustains 58% from three, so a hot run must come down. But a cold run
has an innocent explanation and a guilty one - unlucky, or finished - and no volume gate separates
them, because a player being quietly phased out keeps his snaps for weeks after he stops being
any good. So the under is a bet and the over is a story.

WHAT THE NUMBERS SAID
Validated two ways. Against a decade of gamelogs, the question was whether a projection built from
volume x long-run rate beats the recent-form average at calling the next game, on runs where
conversion ran hot. It does, in every market that ships, by 9-16% of mean absolute error.

Against every real posted line in odds-history, run through --backtest below, which calls the same
assess() this build calls:

                          blind under      recent form       VOLUMES
    NBL     n=151      54.8%  +2.5%      56.7%  +4.8%     68.2%  +26.6%
    NFL     n=71       51.9%  -0.9%      50.8%  -4.4%     60.6%  +14.4%

The two controls matter more than the headline. A prop under carries a structural edge of its own
because public money buys overs - and it is worth +2.5% and -0.9% here, so this is not that edge
wearing a hat. The market is also already anchored to recent form, and firing on recent form alone
is worth +4.8% and -4.4%, so the volume normalisation is what carries it.

Read the hit rates as real and the ROI as indicative. The NBL sits at 68.2% with a standard error
of 3.8, so the true rate is somewhere around 61-76%; the NFL's 60.6% carries an SE of 5.8 and its
lower bound still touches break-even, so the NFL half is promising rather than proven. The
published record is what settles it. An earlier pass of this analysis reported numbers several
points higher from a smaller slice of the same history, which is the ordinary direction for a
thin sample to be wrong in.

MARKETS THAT DO NOT SHIP, AND WHY
Anything whose usual result is zero or one. A market like NHL goals scores 72% on the error test
purely because predicting nearer zero is automatically closer when the answer is almost always
zero - that measures arithmetic, not an edge. Every NHL and EPL market fails this; so do threes,
assists, and AFL goals. The filter is the median output on qualifying rows, and it must be >= 3.
AFL disposals clears that easily at 16 but fails on merit at 53.7%, which is worth knowing: it is
the biggest AFL board by a distance, and summing kicks and handballs averages away the very
conversion variance this signal trades on. Its two halves both ship on their own.

WHERE THE LINE COMES FROM
odds.json, not odds-history - this signal needs the current line and no past one, which is why it
runs for a sport from its first day with no snapshot history at all. Only a two-way rung is used,
and only the one priced closest to even money. An alt ladder carries up to 32 rungs for one player
and market, and taking the line off one row and the price off another produces a number that never
existed - a mistake this project has already made once and published.
"""
import argparse
import datetime as dt
import glob
import json
import os
import statistics
import sys
from collections import defaultdict

# market -> (volume field, volume label, rate label, show the rate as a percentage)
#
# Every pair here cleared two bars: it beats the recent-form average on over-conversion runs by at
# least 55% of cases across the full gamelog history, and its median output on qualifying rows is
# at least 3 so the error test is measuring an edge rather than the pull of zero.
MARKETS = {
    'nba': [
        ('points',   'fga',     'FGA', 'pts/shot', False),
        ('rebounds', 'minutes', 'Min', 'reb/min',  False),
        ('pra',      'minutes', 'Min', 'PRA/min',  False),
        ('pr',       'minutes', 'Min', 'PR/min',   False),
        ('pa',       'minutes', 'Min', 'PA/min',   False),
        ('ra',       'minutes', 'Min', 'RA/min',   False),
        ('threes',   'threesAtt', '3PA', '3P%',    True),
    ],
    'nbl': [
        ('points',   'fga',     'FGA', 'pts/shot', False),
        ('rebounds', 'minutes', 'Min', 'reb/min',  False),
        ('pra',      'minutes', 'Min', 'PRA/min',  False),
        ('pr',       'minutes', 'Min', 'PR/min',   False),
        ('pa',       'minutes', 'Min', 'PA/min',   False),
        ('ra',       'minutes', 'Min', 'RA/min',   False),
        ('threes',   'threesAtt', '3PA', '3P%',    True),
        # assists<-minutes is deliberately absent. It adds 17 rows and they hit about 45%, pulling
        # the NBL from 72.6% to 68.7%. An assist needs a team-mate to finish, so a hot assist rate
        # is partly other people shooting well - not the player's own conversion coming back down.
    ],
    'nfl': [
        ('recYds',     'targets', 'Targets', 'yds/target', False),
        ('receptions', 'targets', 'Targets', 'catch rate', True),
        ('rushYds',    'rushAtt', 'Carries', 'yds/carry',  False),
        ('passYds',    'passAtt', 'Att',     'yds/att',    False),
        # tackles<-snapPct is deliberately absent. It passed the error test at 57.9% and produced
        # 28 of the first 45 NFL rows, and it should not have been in here: a tackle is not a
        # converted attempt. Snap share is playing time, not opportunity taken, so the rows read
        # "0.04 vs 0.04 tackles/snap" - no conversion run at all, just a model disagreeing with
        # the market's base rate. That is a different signal and it is not the one on the tile.
    ],
    'afl': [
        ('kicks',      'tog', 'TOG', 'kicks/min',  False),
        ('handballs',  'tog', 'TOG', 'HB/min',     False),
        ('marks',      'tog', 'TOG', 'marks/min',  False),
        ('dreamteam',  'tog', 'TOG', 'DT/min',     False),
    ],
}

# Where the data lives, which is not always the sport's own name on disk.
DIRS = {'nba': 'nba', 'nbl': 'nbl', 'nfl': 'nfl', 'afl': 'AFL'}

RECENT = 3         # games in the hot run. Measured against 2, 4 and 5; see WINDOW below.
MIN_BASE = 3       # games before it, or there is no long-run rate to compare against
SHRINK = 25.0      # volume units of league-average rate mixed into a thin baseline
VOL_HOLD = 0.75    # the opportunity has to still be there
VOL_CAP = 1.25     # and the hot run must not just be extra volume, which is a real rise
MIN_VOL = 2.0      # below this the rate is noise over a handful of chances
MIN_LINE = 2.0     # a percentage gap on a 0.5 line is not a gap
# The three-week sample cannot tell 0.0, 2.0 and 3.0 apart - 2.0 and 3.0 returned 60.0% and 57.1%
# in the NFL on fifty-odd rows, which is one coin flip of difference. So it is set on principle
# rather than on that wiggle: 2.0 is the lowest floor that still excludes the 0.5 and 1.5 binaries,
# where there is effectively one outcome under the line and a percentage gap means nothing. Raising
# it to 5.0 was the one clearly wrong answer, costing rows in both sports and gaining nothing.
EDGE = 0.10        # the line has to sit this far above the projection

# WINDOW
# Three games, and it is not close. Two is sharper and catches the run earlier, and it returned
# -28.1% in the NFL - two games of a conversion rate is pure noise and the signal fires on it.
# Four and five are steadier and too slow: the market has already moved by the time they notice,
# and the NBL return fell from +22.3% to +9.8% and +8.4%. Three was best or joint-best in both
# sports at every threshold.
#
# THE COHERENCE GATE
# The recent rate must sit above the long-run rate. Without it, a third of NBL rows and nearly half
# of NFL rows fire with the player converting at or BELOW his normal rate - which is not a hot run
# at all, just the model disagreeing with the market's base rate. A different claim, and not the
# one the tile makes. Keeping those rows would have published a number that did not describe the
# thing it was named after, which is the mistake Elite Matchups already made here once.
#
# It also pays for itself, which is not why it is here but is worth recording: the NBL goes from
# 66.7% to 68.0% and the NFL from 58.8% to 62.0%. An earlier pass of this analysis reported that
# the gate ruined the NFL. That was a broken test harness - it compared NFL week keys against date
# strings and so joined every line to the player's first game on record. Hence --backtest below,
# which replays the real snapshots through assess() rather than a copy of it.

AFL_FINALS = {
    'finals week 1': 101,
    'qualifying finals': 101,
    'elimination finals': 101,
    'semi finals': 102,
    'preliminary finals': 103,
    'grand final': 104,
}


def round_no(name):
    """AFL rounds are words, and sorting them as text puts Round 10 before Round 2.

    This is not a detail. The first version of this analysis had no AFL ordering at all and fell
    back to a missing Week field, which silently put every row in one bucket and left the AFL
    market verdicts measuring file order instead of time.
    """
    s = str(name or '').strip().lower()
    if s in AFL_FINALS:
        return AFL_FINALS[s]
    if s.startswith('opening'):
        return 0
    digits = ''.join(c for c in s if c.isdigit())
    return int(digits) if digits else 999


def seq_key(sport, row):
    """One comparable position in time per sport, so 'the last three games' means that."""
    year = str(row.get('Year') or '')
    if sport == 'afl':
        return (year, '%03d' % round_no(row.get('RoundName')), '')
    if sport == 'nfl':
        return (year, '%03d' % int(row.get('Week') or 0), '')
    return (year, str(row.get('Date') or row.get('date') or ''), '')


def load(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def flatten(blob):
    if isinstance(blob, list):
        return [r for r in blob if isinstance(r, dict)]
    if isinstance(blob, dict):
        out = []
        for value in blob.values():
            if isinstance(value, list):
                out += [r for r in value if isinstance(r, dict)]
        return out
    return []


def gamelogs(root, sport):
    """Every season on disk, by player, in time order."""
    folder = DIRS.get(sport, sport)
    rows = []
    for path in sorted(glob.glob(os.path.join(root, folder, 'data', 'gamelogs*.json'))):
        rows += flatten(load(path, []))
    by_player = defaultdict(list)
    for row in rows:
        name = row.get('Player')
        if name:
            by_player[name].append(row)
    for name in by_player:
        by_player[name].sort(key=lambda r: seq_key(sport, r))
    return by_player, rows


def num(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def main_line(odds_rows, markets):
    """The two-way rung priced closest to even money, with the best under price at that rung.

    Two separate decisions, both of which have bitten before. The LINE is the rung the market
    treats as the real one - closest to even money across the ladder. The PRICE is then the best
    one available AT THAT LINE, never the best price anywhere on the ladder, because that pair
    never existed together.
    """
    wanted = {m[0] for m in markets}
    grouped = defaultdict(list)
    for row in odds_rows or []:
        player, market = row.get('player'), row.get('market')
        over, under, line = num(row.get('over')), num(row.get('under')), num(row.get('line'))
        if not player or market not in wanted:
            continue
        if not over or not under or line is None or over <= 1 or under <= 1:
            continue
        grouped[(player, market)].append((line, over, under, row.get('book')))

    out = {}
    for key, rungs in grouped.items():
        line = min(rungs, key=lambda r: abs(1.0 / r[1] - 1.0 / r[2]))[0]
        at_line = [r for r in rungs if r[0] == line]
        best = max(at_line, key=lambda r: r[2])
        out[key] = {'line': line, 'over': best[1], 'under': best[2], 'book': best[3]}
    return out


def league_rates(rows, markets):
    """Conversion rate across the whole league, used as the prior a thin baseline leans on."""
    rates = {}
    for market, vol, _vl, _rl, _pct in markets:
        total_v = total_o = 0.0
        for row in rows:
            v, o = num(row.get(vol)), num(row.get(market))
            if v is None or o is None:
                continue
            total_v += v
            total_o += o
        rates[market] = (total_o / total_v) if total_v > 0 else 0.0
    return rates


def assess(history, market, vol, league_rate):
    """The whole rule, on one player's history. Returns a dict, or a string saying why not.

    Kept separate from build() so the selftest can drive it directly with hand-made histories -
    the refusals are the signal, and they are what needs proving.
    """
    usable = [r for r in history if num(r.get(market)) is not None and num(r.get(vol)) is not None]
    if len(usable) < RECENT + MIN_BASE:
        return 'only %d usable games, need %d' % (len(usable), RECENT + MIN_BASE)

    base, recent = usable[:-RECENT], usable[-RECENT:]
    base_v = [num(r[vol]) for r in base]
    rec_v = [num(r[vol]) for r in recent]
    rec_o = [num(r[market]) for r in recent]
    pool_v = sum(num(r[vol]) for r in usable)
    pool_o = sum(num(r[market]) for r in usable)
    if pool_v <= 0 or sum(rec_v) <= 0:
        return 'no volume on record'

    base_vol = statistics.mean(base_v)
    rec_vol = statistics.mean(rec_v)
    rec_out = statistics.mean(rec_o)
    if rec_vol < MIN_VOL or base_vol < MIN_VOL:
        return 'volume too small to read a rate from (%.1f)' % rec_vol
    if rec_vol < VOL_HOLD * base_vol:
        return 'volume fell to %.0f%% of normal - the line is low for a reason that still holds' % (
            100.0 * rec_vol / base_vol)
    if rec_vol > VOL_CAP * base_vol:
        return 'volume rose to %.0f%% of normal - that is a bigger role, not a hot streak' % (
            100.0 * rec_vol / base_vol)

    # The long-run rate deliberately INCLUDES the hot run itself. Leaving it out would give a
    # cleaner "before" number and a signal that fires more often, because the hot games would stop
    # dragging the rate up. Including them makes every projection a little higher and every sell a
    # little harder to trigger, which is the direction to err in, and it is the version the
    # backtest measured.
    rate = (pool_o + SHRINK * league_rate) / (pool_v + SHRINK)
    return {
        'proj': rate * rec_vol,
        'rate': rate,
        'recRate': sum(rec_o) / sum(rec_v),
        'volBase': base_vol,
        'volRec': rec_vol,
        'recOut': rec_out,
        'baseOut': statistics.mean([num(r[market]) for r in base]),
        'baseGames': len(base),
    }


def build(root, sport, today=None):
    sport = sport.lower()
    markets = MARKETS.get(sport)
    if not markets:
        return {'sport': sport, 'rows': [], 'why': 'no market config for %s' % sport}

    folder = DIRS.get(sport, sport)
    by_player, all_rows = gamelogs(root, sport)
    odds = load(os.path.join(root, folder, 'data', 'odds.json'), {}) or {}
    odds_rows = list(odds.get('lines') or []) + list(odds.get('alt') or [])
    lines = main_line(odds_rows, markets)
    rates = league_rates(all_rows, markets)

    rows, skipped = [], defaultdict(int)
    for (player, market), priced in sorted(lines.items()):
        cfg = next((m for m in markets if m[0] == market), None)
        if not cfg:
            continue
        _m, vol, vol_label, rate_label, rate_pct = cfg
        if priced['line'] < MIN_LINE:
            skipped['line below %.1f' % MIN_LINE] += 1
            continue
        history = by_player.get(player)
        if not history:
            skipped['no gamelogs for the player'] += 1
            continue

        verdict = assess(history, market, vol, rates.get(market, 0.0))
        if isinstance(verdict, str):
            skipped[verdict.split(' - ')[0]] += 1
            continue
        edge = (verdict['proj'] - priced['line']) / priced['line']
        if edge > -EDGE:
            skipped['line within %.0f%% of the projection' % (EDGE * 100)] += 1
            continue
        if verdict['recRate'] <= verdict['rate']:
            skipped['no hot conversion run behind the line'] += 1
            continue

        rows.append({
            'player': player,
            'team': history[-1].get('Team'),
            'market': market,
            'volStat': vol,
            'volLabel': vol_label,
            'rateLabel': rate_label,
            'ratePct': rate_pct,
            'side': 'sell',
            'line': round(priced['line'], 2),
            'over': priced['over'],
            'under': priced['under'],
            'book': priced['book'],
            'proj': round(verdict['proj'], 2),
            'edge': round(edge, 4),
            'rate': round(verdict['rate'], 4),
            'recRate': round(verdict['recRate'], 4),
            'volBase': round(verdict['volBase'], 1),
            'volRec': round(verdict['volRec'], 1),
            'baseOut': round(verdict['baseOut'], 1),
            'recOut': round(verdict['recOut'], 1),
            'baseGames': verdict['baseGames'],
            'games': RECENT,
        })

    rows.sort(key=lambda r: r['edge'])
    return {
        'sport': sport,
        'generated': (today or dt.datetime.now(dt.timezone.utc)).strftime('%Y-%m-%dT%H:%MZ'),
        'window': RECENT,
        'rows': rows,
        'skipped': dict(skipped),
        'why': '' if rows else 'no line sits %.0f%% above its volume projection' % (EDGE * 100),
    }


def backtest(root, sport):
    """Replay odds-history through assess(), the same function the build calls.

    The point is that no second copy of the rule exists to drift from the first. Two separate
    analyses of this signal disagreed with each other because each had its own reimplementation,
    and the one with a bug was the one I believed. Settled by construction here.

    Each game is counted once, at the line from the snapshot closest to it - not once per daily
    snapshot, which would weight a game by how many days it was on the board.
    """
    sport = sport.lower()
    markets = MARKETS.get(sport) or []
    folder = DIRS.get(sport, sport)
    by_player, all_rows = gamelogs(root, sport)
    rates = league_rates(all_rows, markets)

    index = {}
    for name, rows in by_player.items():
        for i, row in enumerate(rows):
            index[(name, seq_key(sport, row))] = i

    cand = {}
    for path in sorted(glob.glob(os.path.join(root, folder, 'odds-history', '*.json.gz'))):
        day = os.path.basename(path)[:10]
        try:
            import gzip as gz
            with gz.open(path) as fh:
                snap = json.load(fh)
        except Exception:
            continue
        fixtures = snap.get('fixture') or []
        weeks = [f.get('week') for f in fixtures if f.get('week')]
        dates = sorted({f.get('date') for f in fixtures if f.get('date')})
        if sport == 'nfl':
            if not weeks:
                continue
            target = (str(snap.get('season') or dates[0][:4] if dates else '2026'),
                      '%03d' % int(statistics.mode(weeks)), '')
        for (player, market), priced in main_line(snap.get('odds') or [], markets).items():
            if sport != 'nfl':
                later = [r for r in by_player.get(player, [])
                         if seq_key(sport, r)[1] >= day]
                if not later:
                    continue
                target = seq_key(sport, later[0])
            cand[(player, market, target)] = priced

    bets, blind, form = [], [], []
    for (player, market, target), priced in cand.items():
        cfg = next((m for m in markets if m[0] == market), None)
        if not cfg or priced['line'] < MIN_LINE:
            continue
        i = index.get((player, target))
        rows = by_player.get(player) or []
        if i is None or i == 0:
            continue
        verdict = assess(rows[:i], market, cfg[1], rates.get(market, 0.0))
        if isinstance(verdict, str):
            continue
        actual = num(rows[i].get(market))
        if actual is None:
            continue
        won = actual < priced['line']

        # The two controls, on exactly these rows. Without them a good-looking return means
        # nothing: prop unders carry a structural edge of their own because public money buys
        # overs, and the market is already anchored to recent form. The signal has to beat both,
        # not just break even against the price.
        blind.append((won, priced['under']))
        if (verdict['recOut'] - priced['line']) / priced['line'] < -EDGE:
            form.append((won, priced['under']))

        edge = (verdict['proj'] - priced['line']) / priced['line']
        if edge > -EDGE or verdict['recRate'] <= verdict['rate']:
            continue
        bets.append((won, priced['under'], market))

    if not bets:
        print('%s: nothing resolvable - needs snapshots with games played after them' % sport)
        return 0

    def line(label, rows):
        if not rows:
            return
        hit = 100.0 * sum(1 for w, *_ in rows if w) / len(rows)
        units = sum((p - 1) if w else -1 for w, p, *_ in rows)
        se = (hit * (100 - hit) / len(rows)) ** 0.5
        print('   %-26s n=%-4d hit %5.1f%% (SE %.1f)  %+6.1fu  (%+5.1f%% ROI)'
              % (label, len(rows), hit, se, units, 100.0 * units / len(rows)))

    print('%s' % sport)
    line('blind under, every row', blind)
    line('recent form below line', form)
    line('VOLUMES', bets)
    print('   by market:')
    by_market = defaultdict(list)
    for w, p, m in bets:
        by_market[m].append((w, p))
    for market in sorted(by_market):
        v = by_market[market]
        h = 100.0 * sum(1 for w, _ in v if w) / len(v)
        u = sum((p - 1) if w else -1 for w, p in v)
        flag = '' if len(v) >= 30 else '   (too few to read)'
        print('      %-12s n=%-4d hit %5.1f%%  %+6.1fu%s' % (market, len(v), h, u, flag))
    return 0


def selftest():
    """Synthetic cases. The refusals are the point, so most of these prove it says no."""
    fails = []

    def ok(label, cond, note=''):
        mark = 'ok  ' if cond else 'FAIL'
        print('   %s %s%s' % (mark, label, ('   ' + note) if note else ''))
        if not cond:
            fails.append(label)

    def games(pairs, market='points', vol='fga', year='2026'):
        return [{'Player': 'T', 'Team': 'AAA', 'Year': year, 'Date': '2026-01-%02d' % (i + 1),
                 market: o, vol: v} for i, (v, o) in enumerate(pairs)]

    print('round ordering')
    ok('Round 2 comes before Round 10', round_no('Round 2') < round_no('Round 10'))
    ok('Opening Round comes first', round_no('Opening Round') < round_no('Round 1'))
    ok('the Grand Final comes last',
       round_no('Grand Final') > round_no('Round 24') and
       round_no('Grand Final') > round_no('Preliminary Finals'))
    ok('a round it has never seen sorts late, not first', round_no('Wildcard Week') == 999)

    print('the main line and its price')
    rungs = [
        {'player': 'T', 'market': 'points', 'line': 10.5, 'over': 1.30, 'under': 3.40, 'book': 'A'},
        {'player': 'T', 'market': 'points', 'line': 15.5, 'over': 1.92, 'under': 1.88, 'book': 'B'},
        {'player': 'T', 'market': 'points', 'line': 15.5, 'over': 1.80, 'under': 1.95, 'book': 'C'},
        {'player': 'T', 'market': 'points', 'line': 24.5, 'over': 4.50, 'under': 1.22, 'book': 'D'},
    ]
    got = main_line(rungs, MARKETS['nbl'])[('T', 'points')]
    ok('it takes the rung closest to even money', got['line'] == 15.5,
       '(not the 1.22 under on 24.5, which would look like a free bet)')
    ok('and the best under price AT that rung', got['under'] == 1.95 and got['book'] == 'C',
       '(got $%.2f at %s)' % (got['under'], got['book']))
    one_way = [{'player': 'T', 'market': 'points', 'line': 15.5, 'over': 1.9, 'under': None}]
    ok('a one-way rung is not a line', ('T', 'points') not in main_line(one_way, MARKETS['nbl']))

    print('the rule')
    # 6 games at 10 shots: 5 normal at 1.0 pts/shot, then 3 hot at 1.6
    hot = games([(10, 10), (10, 10), (10, 10), (10, 10), (10, 10), (10, 16), (10, 16), (10, 16)])
    v = assess(hot, 'points', 'fga', 1.0)
    ok('a hot run with volume held is assessed', isinstance(v, dict))
    ok('and projects below what he has just been scoring', v['proj'] < v['recOut'],
       '(proj %.1f vs %.1f recent)' % (v['proj'], v['recOut']))
    ok('and reports the hot rate alongside the long-run one', v['recRate'] > v['rate'],
       '(%.2f vs %.2f)' % (v['recRate'], v['rate']))

    print('the refusals, which are the whole signal')
    grew = games([(10, 10)] * 5 + [(20, 32), (20, 32), (20, 32)])
    r = assess(grew, 'points', 'fga', 1.0)
    ok('doubled volume is refused as a bigger role, not a hot streak',
       isinstance(r, str) and 'bigger role' in r, '(%s)' % r)
    collapsed = games([(10, 10)] * 5 + [(4, 7), (4, 7), (4, 7)])
    r = assess(collapsed, 'points', 'fga', 1.0)
    ok('collapsed volume is refused even though the rate is hot',
       isinstance(r, str) and 'still holds' in r, '(%s)' % r)
    tiny = games([(1, 1)] * 8)
    r = assess(tiny, 'points', 'fga', 1.0)
    ok('a handful of chances is refused as unreadable',
       isinstance(r, str) and 'too small' in r, '(%s)' % r)
    short = games([(10, 10)] * 4)
    r = assess(short, 'points', 'fga', 1.0)
    ok('too few games is refused rather than guessed',
       isinstance(r, str) and 'need' in r, '(%s)' % r)

    print('the thin-baseline prior')
    # One freak game should not become a career rate. 6 games, the first five at 1.0, and a
    # baseline of only three games means the shrink has real work to do.
    freak = games([(10, 30), (10, 10), (10, 10), (10, 16), (10, 16), (10, 16)])
    v = assess(freak, 'points', 'fga', 1.0)
    raw = 98 / 60.0
    ok('a thin baseline is pulled toward the league rate',
       isinstance(v, dict) and v['rate'] < raw - 0.1 and v['rate'] > 1.0,
       '(rate %.2f, raw %.2f, league 1.00)' % (v['rate'], raw))

    print('end to end, on whatever is staged')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for sport in ('nbl', 'nba', 'nfl', 'afl'):
        out = build(root, sport)
        shaped = (isinstance(out.get('rows'), list)
                  and all(r['line'] >= MIN_LINE and r['edge'] <= -EDGE and r['side'] == 'sell'
                          for r in out['rows']))
        ok('%s builds a well-formed file' % sport, shaped,
           '(%d rows%s)' % (len(out.get('rows') or []),
                            '; ' + out['why'] if out.get('why') else ''))

    print('\n%s' % ('all checks passed' if not fails else '%d FAILED: %s' % (len(fails), fails)))
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('sport', nargs='?', help='nba, nbl, nfl or afl')
    ap.add_argument('--print', dest='show', action='store_true', help='show rows, write nothing')
    ap.add_argument('--backtest', action='store_true',
                    help='replay odds-history through the same rule the build uses')
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.sport:
        ap.error('a sport, or --selftest')

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sport = args.sport.lower()

    if args.backtest:
        return backtest(root, sport)
    out = build(root, sport)

    if args.show:
        print('%s: %d rows%s' % (sport, len(out['rows']), '  ' + out['why'] if out['why'] else ''))
        for r in out['rows']:
            rate = ('%.0f%% vs %.0f%%' % (r['recRate'] * 100, r['rate'] * 100) if r['ratePct']
                    else '%.2f vs %.2f %s' % (r['recRate'], r['rate'], r['rateLabel']))
            print('   %-24s U%-6.1f %-11s $%.2f  proj %6.2f (%+5.1f%%)  %s %.1f/%.1f  %s'
                  % (r['player'][:24], r['line'], r['market'], r['under'], r['proj'],
                     r['edge'] * 100, r['volLabel'], r['volRec'], r['volBase'], rate))
        if out.get('skipped'):
            print('   not shown:')
            for why, n in sorted(out['skipped'].items(), key=lambda kv: -kv[1]):
                print('      %-58s %d' % (why, n))
        return 0

    folder = DIRS.get(sport, sport)
    path = os.path.join(root, folder, 'data', 'volumes.json')
    with open(path, 'w') as fh:
        json.dump(out, fh, separators=(',', ':'))
    print('wrote %s: %d rows%s' % (path, len(out['rows']), '  ' + out['why'] if out['why'] else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
